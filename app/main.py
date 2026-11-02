from __future__ import annotations

import json
import logging
import mimetypes
import random
import shutil
import threading
import traceback
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import (
    APP_NAME,
    APP_VERSION,
    FFMPEG_BIN,
    FFPROBE_BIN,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    PACKAGE_ROOT,
    SUPPORTED_AUDIO_EXTENSIONS,
    SUPPORTED_VIDEO_EXTENSIONS,
    VOICE_CANDIDATES_DEFAULT,
)
from app.models import (
    DirectedEvidenceSearchRequest,
    EditPlan,
    JobState,
    ManualEvidenceRequest,
    ProjectKnowledge,
    ProjectCreate,
    ProfessionalSyncPlan,
    PublicationPackage,
    VideoAnalysis,
    VoiceGenerateRequest,
)
from app.services.assembler import assemble_project
from app.services.directed_evidence import add_manual_evidence, search_directed_evidence
from app.services.ffmpeg import (
    audio_duration as probe_audio_duration,
    convert_recording_to_wav,
    create_proxy,
    create_verification_proxy,
    executable_exists,
    prepare_voice_reference,
    probe_media,
    normalize_voice_audio,
    safe_speech_bounds,
)
from app.services.gemini import (
    analyze_video, create_edit_plan, create_publication_package, infer_footage_chronology,
)
from app.services.professional_sync import (
    ProfessionalSyncError,
    apply_manual_event_override,
    build_professional_sync,
    recheck_existing_sync,
    catalog_is_professional,
    flatten_evidence,
)
from app.services.project import (
    archive_render_files,
    create_project,
    list_projects,
    load_knowledge,
    load_settings,
    now_iso,
    ordered_footage,
    open_folder,
    paths_for,
    read_json,
    save_knowledge,
    save_settings,
    write_json,
)
from app.services.voice_client import (
    apply_pronunciations,
    export_voice,
    generate_voice,
    voice_health,
)
from app.services.voice_profile import (
    default_pronunciations,
    load_voice_profile,
    profile_root,
    save_voice_profile,
)

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "app" / "static"), name="static")

executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="drsaygon")
jobs: dict[tuple[str, str], JobState] = {}
job_lock = threading.Lock()
job_progress_mirrors: dict[tuple[str, str], dict[str, Any]] = {}
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(APP_NAME)

JOB_NAMES = ("analysis", "script", "voice", "evidence_search", "sync", "assembly", "publication", "autopilot")


def job_key(slug: str, name: str) -> tuple[str, str]:
    return slug, name


def set_job(slug: str, name: str, **changes: Any) -> JobState:
    """Update a job and, when running under Autopilot, mirror real child progress.

    v6.0 left the Autopilot bar parked at 62% while sync performed dozens of
    expensive verification/search operations. The child job was progressing;
    the parent simply did not know. Mirrors keep one truthful, monotonic progress
    bar without coupling the individual workers to the UI.
    """
    timestamp = now_iso()
    with job_lock:
        key = job_key(slug, name)
        current = jobs.get(key) or JobState(project_slug=slug, job=name)  # type: ignore[arg-type]
        updated = current.model_copy(update={**changes, "updated_at": timestamp})
        jobs[key] = updated

        mirror = job_progress_mirrors.get(key)
        if mirror:
            parent_name = str(mirror["parent"])
            parent_key = job_key(slug, parent_name)
            parent = jobs.get(parent_key) or JobState(project_slug=slug, job=parent_name)  # type: ignore[arg-type]
            start = int(mirror["start"])
            end = int(mirror["end"])
            child_progress = max(0, min(100, int(updated.progress)))
            mapped = start + int(round((end - start) * child_progress / 100.0))
            mapped = max(int(parent.progress), min(end, mapped))
            detail = updated.message or updated.status
            label = str(mirror.get("label") or name)
            parent_message = f"{label} · {detail}" if detail else label
            jobs[parent_key] = parent.model_copy(update={
                "status": "running",
                "progress": mapped,
                "message": parent_message,
                "error": None,
                "updated_at": timestamp,
            })
        return updated


@contextmanager
def mirror_job_progress(
    slug: str,
    child: str,
    parent: str,
    start: int,
    end: int,
    label: str,
):
    """Mirror 0..100 child progress into a slice of the parent progress bar."""
    key = job_key(slug, child)
    with job_lock:
        job_progress_mirrors[key] = {
            "parent": parent,
            "start": max(0, min(100, int(start))),
            "end": max(0, min(100, int(end))),
            "label": label,
        }
    try:
        yield
    finally:
        with job_lock:
            job_progress_mirrors.pop(key, None)


def append_log(slug: str, message: str) -> None:
    paths = paths_for(slug)
    paths.logs.mkdir(parents=True, exist_ok=True)
    line = f"{now_iso()} | {message}\n"
    with (paths.logs / "atividade.log").open("a", encoding="utf-8") as handle:
        handle.write(line)
    logger.info("[%s] %s", slug, message)


def load_prompt(name: str) -> str:
    return (PACKAGE_ROOT / "prompts" / name).read_text(encoding="utf-8")


def archive_current_result(slug: str, reason: str = "") -> None:
    """Archive every current output together so the UI never exposes stale files."""
    paths = paths_for(slug)
    stamp = now_iso().replace(":", "-").replace("+", "_").replace(".", "-")
    archived = archive_render_files(paths, stamp)
    outputs = list(archived.get("outputs", []))
    report = str(archived.get("report", "") or "")
    if not outputs and not report:
        return
    history = paths.result / "HISTORICO"
    write_json(history / f"ARQUIVAMENTO_{stamp}.json", {
        "archived_at": now_iso(),
        "reason": reason or "dados de origem alterados",
        "outputs": outputs,
        "report": report,
    })
    append_log(slug, f"Saída(s) anterior(es) preservada(s) em HISTORICO: {', '.join(outputs) or report}")


def invalidate_sync(slug: str, reason: str = "") -> None:
    paths = paths_for(slug)
    sync_path = paths.analysis / "sincronismo_profissional.json"
    if sync_path.exists():
        sync_path.unlink(missing_ok=True)
        append_log(slug, f"Sincronismo invalidado: {reason or 'dados de origem alterados'}")
    archive_current_result(slug, reason or "sincronismo invalidado")


def information_files(slug: str) -> list[Path]:
    return [path for path in sorted(paths_for(slug).information.iterdir()) if path.is_file()]


def collect_information_text(slug: str) -> str:
    sections: list[str] = []
    for file in information_files(slug):
        if file.suffix.lower() in {".txt", ".md"}:
            try:
                sections.append(f"\n--- {file.name} ---\n{file.read_text(encoding='utf-8')}")
            except UnicodeDecodeError:
                sections.append(f"\n--- {file.name} ---\n[Arquivo nao pode ser lido como UTF-8]")
        elif file.suffix.lower() == ".pdf":
            sections.append(f"\n--- {file.name} ---\n[PDF anexado e enviado ao Gemini como fonte do roteiro]")
    return "\n".join(sections).strip()


def sanitize_analysis(analysis: VideoAnalysis, duration: float) -> VideoAnalysis:
    cleaned = []
    for segment in analysis.segments:
        start = min(max(segment.start_seconds, 0.0), duration)
        end = min(max(segment.end_seconds, 0.0), duration)
        if end - start < 0.15:
            continue
        segment.start_seconds = round(start, 3)
        segment.end_seconds = round(end, 3)
        segment.focus_x = min(max(segment.focus_x, 0), 1)
        segment.focus_y = min(max(segment.focus_y, 0), 1)
        cleaned.append(segment)
    analysis.duration_seconds = duration
    analysis.segments = sorted(cleaned, key=lambda item: item.start_seconds)
    return analysis


def assign_evidence_ids(analysis: VideoAnalysis, video_index: int) -> VideoAnalysis:
    for segment_index, segment in enumerate(analysis.segments, start=1):
        segment.event_id = f"v{video_index:03d}_e{segment_index:03d}"
        segment.evidence_timestamps = [
            round(min(max(float(ts), segment.start_seconds), segment.end_seconds), 3)
            for ts in segment.evidence_timestamps
            if segment.start_seconds <= float(ts) <= segment.end_seconds
        ]
        if not segment.evidence_timestamps and segment.critical_action:
            segment.evidence_timestamps = [round((segment.start_seconds + segment.end_seconds) / 2, 3)]
    return analysis


def _catalog_lookup(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["filename"]: item for item in catalog.get("videos", [])}


def _matching_segment(video: dict[str, Any], start: float, end: float) -> dict[str, Any] | None:
    candidates = video.get("segments", [])
    best = None
    best_overlap = 0.0
    for segment in candidates:
        overlap = max(0.0, min(end, float(segment["end_seconds"])) - max(start, float(segment["start_seconds"])))
        if overlap > best_overlap:
            best_overlap = overlap
            best = segment
    return best


def sanitize_plan(plan: EditPlan, catalog: dict[str, Any], target_duration: int) -> EditPlan:
    lookup = _catalog_lookup(catalog)
    all_valid_segments: list[tuple[str, dict[str, Any]]] = []
    for filename, video in lookup.items():
        for segment in video.get("segments", []):
            if segment.get("use", True):
                all_valid_segments.append((filename, segment))
    if not all_valid_segments:
        raise RuntimeError("O catalogo nao contem nenhuma tomada marcada como utilizavel")

    for block_index, block in enumerate(plan.blocks):
        valid_clips = []
        for clip in block.clips:
            video = lookup.get(clip.filename)
            if not video:
                continue
            duration = float(video.get("duration_seconds", 0))
            clip.start_seconds = min(max(clip.start_seconds, 0), duration)
            clip.end_seconds = min(max(clip.end_seconds, 0), duration)
            if clip.end_seconds - clip.start_seconds < 0.15:
                continue
            segment = _matching_segment(video, clip.start_seconds, clip.end_seconds)
            if segment:
                clip.focus_x = float(segment.get("focus_x", clip.focus_x))
                clip.focus_y = float(segment.get("focus_y", clip.focus_y))
                clip.vertical_strategy = segment.get("vertical_strategy", clip.vertical_strategy)
                clip.shot_style = segment.get("shot_style", clip.shot_style)
                clip.min_take_seconds = float(segment.get("min_take_seconds", clip.min_take_seconds))
                clip.max_take_seconds = float(segment.get("max_take_seconds", clip.max_take_seconds))
                if segment.get("original_audio_is_important") and clip.audio_strategy == "ambient":
                    clip.audio_strategy = "feature"
            valid_clips.append(clip)

        if not valid_clips:
            raise RuntimeError(
                f"O bloco {block.number} ficou sem nenhuma tomada valida do catalogo. "
                "O Video Factory nao injeta uma cena aleatoria de seguranca."
            )
        block.clips = valid_clips
    plan.target_duration_seconds = target_duration
    return plan


def run_analysis(slug: str) -> None:
    set_job(slug, "analysis", status="running", progress=1, message="Iniciando analise", error=None)
    paths = paths_for(slug)
    try:
        videos = [
            path for path in ordered_footage(slug)
            if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS
        ]
        if not videos:
            raise RuntimeError("Nenhuma filmagem compativel foi encontrada em 01_FILMAGENS")
        prompt = load_prompt("analisar_video.txt")
        knowledge = load_knowledge(slug)
        settings = load_settings(slug)
        analyses: list[dict[str, Any]] = []
        models_used: list[str] = []
        for index, video in enumerate(videos, start=1):
            percent = int((index - 1) / len(videos) * 90) + 2
            set_job(slug, "analysis", progress=percent, message=f"Analisando {video.name} ({index}/{len(videos)})")
            info = probe_media(video)
            proxy = paths.temp / "proxies" / f"proxy_{index:03d}.mp4"
            create_proxy(video, proxy)
            analysis, model = analyze_video(
                proxy_path=proxy,
                original_filename=video.name,
                original_duration=info.duration,
                prompt_text=prompt,
                logger=lambda msg: append_log(slug, msg),
                context_text=json.dumps({
                    "sequence_index": index,
                    "sequence_total": len(videos),
                    "chronology_mode": knowledge.chronology_mode,
                    "locked_facts": knowledge.locked_facts,
                    "chronology_notes": knowledge.chronology_notes,
                    "audio_mode": settings.audio_mode,
                }, ensure_ascii=False, indent=2),
            )
            analysis = sanitize_analysis(analysis, info.duration)
            analysis = assign_evidence_ids(analysis, index)
            analysis.sequence_index = index
            write_json(paths.analysis / "videos" / f"{video.stem}.json", analysis.model_dump(mode="json"))
            analyses.append(analysis.model_dump(mode="json"))
            models_used.append(model)
            append_log(slug, f"Analise concluida: {video.name}")
        write_json(paths.analysis / "catalogo_cenas.json", {
            "project": settings.model_dump(mode="json"),
            "models_used": sorted(set(models_used)),
            "analysis_mode": "autopilot_evidence_v6",
            "knowledge": knowledge.model_dump(mode="json"),
            "videos": analyses,
        })
        invalidate_sync(slug, "filmagens reanalisadas")
        set_job(slug, "analysis", status="completed", progress=100, message="Filmagens analisadas com evidencias de alta precisao")
    except Exception as error:
        append_log(slug, traceback.format_exc())
        set_job(slug, "analysis", status="failed", progress=0, message="Falha na analise", error=str(error))


def _invalidate_voice_for_changed_blocks(slug: str, old: EditPlan | None, new: EditPlan) -> None:
    if old is None:
        return
    paths = paths_for(slug)
    old_map = {b.number: b.narration for b in old.blocks}
    for block in new.blocks:
        if old_map.get(block.number, "") != block.narration:
            (paths.narration / f"bloco_{block.number:03d}.wav").unlink(missing_ok=True)
            (paths.narration / f"bloco_{block.number:03d}.approved.json").unlink(missing_ok=True)
            candidate_dir = paths.narration / "ia" / f"bloco_{block.number:03d}"
            if candidate_dir.exists():
                shutil.rmtree(candidate_dir)


def run_script_generation(slug: str) -> None:
    set_job(slug, "script", status="running", progress=5, message="Preparando catalogo", error=None)
    paths = paths_for(slug)
    try:
        settings = load_settings(slug)
        knowledge = load_knowledge(slug)
        catalog = apply_knowledge_to_catalog(
            slug, read_json(paths.analysis / "catalogo_cenas.json"), knowledge
        )
        if not catalog:
            raise RuntimeError("Analise as filmagens antes de gerar o roteiro")
        write_json(paths.analysis / "catalogo_cenas.json", catalog)
        set_job(slug, "script", progress=25, message="Criando roteiro e sincronismo visual")
        plan, model = create_edit_plan(
            catalog=catalog,
            project_data=settings.model_dump(mode="json"),
            information_text=collect_information_text(slug),
            information_files=information_files(slug),
            prompt_text=load_prompt("criar_roteiro.txt"),
            logger=lambda msg: append_log(slug, msg),
            knowledge_data=knowledge.model_dump(mode="json"),
        )
        plan = sanitize_plan(plan, catalog, settings.target_duration_seconds)
        old_data = read_json(paths.analysis / "roteiro.json")
        old_plan = EditPlan.model_validate(old_data) if old_data else None
        _invalidate_voice_for_changed_blocks(slug, old_plan, plan)
        write_json(paths.analysis / "roteiro.json", plan.model_dump(mode="json"))
        invalidate_sync(slug, "roteiro regenerado")
        readable = []
        for block in plan.blocks:
            narration = block.narration or "[SEM NARRACAO - DEMONSTRACAO/AMBIENTE]"
            readable.append(f"BLOCO {block.number:03d} - {block.title}\n{narration}\n")
        (paths.analysis / "ROTEIRO_PARA_LEITURA.txt").write_text("\n".join(readable), encoding="utf-8")
        append_log(slug, f"Roteiro criado com o modelo {model}")
        set_job(slug, "script", status="completed", progress=100, message="Roteiro pronto")
    except Exception as error:
        append_log(slug, traceback.format_exc())
        set_job(slug, "script", status="failed", progress=0, message="Falha ao criar roteiro", error=str(error))

def run_voice_generation(slug: str, block_number: int | None, request: VoiceGenerateRequest) -> None:
    set_job(slug, "voice", status="running", progress=1, message="Preparando voz clonada", error=None, block_number=block_number)
    paths = paths_for(slug)
    try:
        profile = load_voice_profile()
        embedding = Path(profile.embedding_path) if profile.embedding_path else None
        if not profile.prepared or not embedding or not embedding.exists():
            raise RuntimeError("Configure sua voz em 'Minha voz' antes de gerar as narracoes")
        plan_data = read_json(paths.analysis / "roteiro.json")
        if not plan_data:
            raise RuntimeError("Gere o roteiro antes da voz")
        plan = EditPlan.model_validate(plan_data)
        blocks = [b for b in plan.blocks if b.needs_voice and (block_number is None or b.number == block_number)]
        if not blocks:
            raise RuntimeError("Nenhum bloco com narracao foi encontrado para gerar")
        total = len(blocks) * request.candidates
        done = 0
        for block in blocks:
            candidate_dir = paths.narration / "ia" / f"bloco_{block.number:03d}"
            if candidate_dir.exists():
                shutil.rmtree(candidate_dir)
            candidate_dir.mkdir(parents=True, exist_ok=True)
            spoken_text = apply_pronunciations(block.narration, profile.pronunciation_rules)
            metadata: list[dict[str, Any]] = []
            for candidate in range(1, request.candidates + 1):
                done += 1
                progress = max(2, int((done - 1) / total * 96))
                set_job(
                    slug, "voice", progress=progress, block_number=block_number,
                    message=f"Gerando voz: bloco {block.number}, alternativa {candidate}/{request.candidates}",
                )
                seed = random.SystemRandom().randint(1000, 2_000_000_000)
                output = candidate_dir / f"candidato_{candidate:02d}.wav"
                raw_output = candidate_dir / f"candidato_{candidate:02d}_raw.wav"
                info = generate_voice(spoken_text, embedding, raw_output, request.quality, seed)
                normalize_voice_audio(raw_output, output)
                raw_output.unlink(missing_ok=True)
                duration = probe_audio_duration(output)
                words = max(1, len(spoken_text.split()))
                seconds_per_word = duration / words
                quality_warning = ""
                if seconds_per_word < 0.16 or seconds_per_word > 1.05:
                    quality_warning = "Duracao fora da faixa esperada; ouca com atencao antes de aprovar."
                metadata.append({
                    "candidate": candidate,
                    "filename": output.name,
                    "quality": request.quality,
                    "seed": seed,
                    "duration_seconds": round(duration, 3),
                    "seconds_per_word": round(seconds_per_word, 3),
                    "quality_warning": quality_warning,
                    "spoken_text": spoken_text,
                })
                append_log(slug, f"Voz gerada: bloco {block.number}, candidato {candidate}")
            write_json(candidate_dir / "candidatos.json", {"items": metadata})
        set_job(slug, "voice", status="completed", progress=100, message="Alternativas de voz prontas para aprovacao", block_number=block_number)
    except Exception as error:
        append_log(slug, traceback.format_exc())
        set_job(slug, "voice", status="failed", progress=0, message="Falha ao gerar voz", error=str(error), block_number=block_number)


def run_sync(slug: str) -> None:
    set_job(slug, "sync", status="running", progress=1, message="Preparando sincronismo profissional", error=None)
    paths = paths_for(slug)
    try:
        settings = load_settings(slug)
        knowledge = load_knowledge(slug)
        raw_plan = read_json(paths.analysis / "roteiro.json")
        catalog = read_json(paths.analysis / "catalogo_cenas.json")
        if not raw_plan:
            raise RuntimeError("Gere o roteiro antes do sincronismo")
        if not catalog:
            raise RuntimeError("Analise as filmagens antes do sincronismo")
        plan = EditPlan.model_validate(raw_plan)
        sync = build_professional_sync(
            paths=paths,
            plan=plan,
            catalog=catalog,
            sync_prompt=load_prompt("criar_sincronismo.txt"),
            verify_prompt=load_prompt("verificar_evento.txt"),
            alignment_prompt=load_prompt("alinhar_narracao.txt"),
            logger=lambda msg: append_log(slug, msg),
            progress=lambda value, msg: set_job(slug, "sync", progress=value, message=msg),
            settings=settings,
            knowledge=knowledge,
            directed_prompt=load_prompt("buscar_evidencia_dirigida.txt"),
            rough_review_prompt=load_prompt("revisar_rascunho.txt"),
        )
        if sync.ready:
            set_job(slug, "sync", status="completed", progress=100, message="Corte profissional verificado e pronto para render")
        else:
            hard_failed = [
                beat for beat in sync.beats
                if beat.verification == "failed" and getattr(beat, "visual_obligation", "contextual") == "hard"
            ]
            if hard_failed:
                labels = " | ".join(f"{beat.beat_id}: {beat.text}" for beat in hard_failed[:3])
                error = (
                    f"Restaram {len(hard_failed)} ação(ões)/fenômeno(s) realmente essenciais sem prova visual após "
                    f"todas as tentativas automáticas. {labels}"
                )
            elif sync.rough_cut_qa and not sync.rough_cut_qa.approved:
                error = "O crítico do rascunho encontrou um problema editorial real após a montagem automática."
            else:
                error = "O corte não atingiu o limiar de publicação automática. Consulte os avisos do mapa."
            set_job(
                slug, "sync", status="failed", progress=100,
                message="Autopilot esgotou as correções automáticas desta exceção",
                error=error,
            )
    except Exception as error:
        append_log(slug, traceback.format_exc())
        set_job(slug, "sync", status="failed", progress=0, message="Falha no sincronismo", error=str(error))


def run_sync_review_only(slug: str) -> None:
    """Re-run only global rough-cut QA after an explicit human shot override."""
    set_job(slug, "sync", status="running", progress=86, message="Revalidando corte apos ajuste manual", error=None)
    paths = paths_for(slug)
    try:
        settings = load_settings(slug)
        knowledge = load_knowledge(slug)
        raw_plan = read_json(paths.analysis / "roteiro.json")
        raw_sync = read_json(paths.analysis / "sincronismo_profissional.json")
        if not raw_plan or not raw_sync:
            raise RuntimeError("Roteiro/sincronismo ausente para revalidacao")
        plan = EditPlan.model_validate(raw_plan)
        sync = ProfessionalSyncPlan.model_validate(raw_sync)
        set_job(slug, "sync", progress=92, message="Renderizando rascunho e executando QA global")
        sync = recheck_existing_sync(
            paths, settings, plan, sync, knowledge, load_prompt("revisar_rascunho.txt"),
            logger=lambda msg: append_log(slug, msg),
        )
        if sync.ready:
            set_job(slug, "sync", status="completed", progress=100, message="Ajuste manual revalidado pelo corte global", error=None)
        else:
            set_job(
                slug, "sync", status="failed", progress=100,
                message="O corte alterado ainda precisa de revisao",
                error="O QA global encontrou problema depois da troca manual. Veja o mapa.",
            )
    except Exception as error:
        append_log(slug, traceback.format_exc())
        set_job(slug, "sync", status="failed", progress=0, message="Falha ao revalidar ajuste manual", error=str(error))


def run_assembly(slug: str) -> None:
    set_job(slug, "assembly", status="running", progress=1, message="Iniciando montagem", error=None)
    paths = paths_for(slug)
    try:
        settings = load_settings(slug)
        raw_plan = read_json(paths.analysis / "roteiro.json")
        if not raw_plan:
            raise RuntimeError("Gere o roteiro antes de montar o video")
        plan = EditPlan.model_validate(raw_plan)
        raw_sync = read_json(paths.analysis / "sincronismo_profissional.json")
        if not raw_sync:
            raise RuntimeError(
                "Prepare o sincronismo profissional antes de montar. "
                "Na etapa 6, clique em 'Preparar sincronismo profissional'."
            )
        sync = ProfessionalSyncPlan.model_validate(raw_sync)
        if not sync.ready:
            raise RuntimeError("O gate anti-alucinacao bloqueou a montagem porque ha batidas sem evidencia valida")
        final_path = assemble_project(
            paths=paths,
            settings=settings,
            plan=plan,
            sync=sync,
            logger=lambda msg: append_log(slug, msg),
            progress=lambda value, msg: set_job(slug, "assembly", progress=value, message=msg),
        )
        append_log(slug, f"Render concluído: {final_path}")
        set_job(slug, "assembly", status="completed", progress=100, message="Saídas de vídeo concluídas")
    except Exception as error:
        append_log(slug, traceback.format_exc())
        set_job(slug, "assembly", status="failed", progress=0, message="Falha na montagem", error=str(error))


def run_publication(slug: str) -> None:
    set_job(slug, "publication", status="running", progress=10, message="Criando textos de publicacao", error=None)
    paths = paths_for(slug)
    try:
        settings = load_settings(slug)
        raw_plan = read_json(paths.analysis / "roteiro.json")
        if not raw_plan:
            raise RuntimeError("Gere o roteiro antes do pacote de publicacao")
        plan = EditPlan.model_validate(raw_plan)
        package, model = create_publication_package(
            plan=plan,
            project_data=settings.model_dump(mode="json"),
            information_text=collect_information_text(slug),
            prompt_text=load_prompt("criar_publicacao.txt"),
            logger=lambda msg: append_log(slug, msg),
            progress=lambda value, msg: set_job(slug, "publication", progress=value, message=msg),
        )
        write_json(paths.publication / "PUBLICACAO.json", package.model_dump(mode="json"))
        (paths.publication / "TITULO.txt").write_text(package.recommended_title, encoding="utf-8")
        (paths.publication / "DESCRICAO.txt").write_text(package.description, encoding="utf-8")
        (paths.publication / "COMENTARIO_FIXADO.txt").write_text(package.pinned_comment, encoding="utf-8")
        (paths.publication / "TAGS.txt").write_text(", ".join(package.tags), encoding="utf-8")
        if package.research_sources:
            source_lines = [f"{source.title or 'Fonte oficial'}\n{source.url}" for source in package.research_sources if source.url]
            (paths.publication / "FONTES_PESQUISADAS.txt").write_text("\n\n".join(source_lines), encoding="utf-8")
        append_log(slug, f"Pacote de publicacao v6.0 criado com {model}; {len(package.tags)} tags; pesquisa={package.research_status}")
        set_job(slug, "publication", status="completed", progress=100, message="Textos de publicacao prontos")
    except Exception as error:
        append_log(slug, traceback.format_exc())
        set_job(slug, "publication", status="failed", progress=0, message="Falha na publicacao", error=str(error))



def _approved_voice_ready(slug: str, plan: EditPlan) -> bool:
    settings = load_settings(slug)
    if settings.audio_mode == "original":
        return True
    paths = paths_for(slug)
    return all(
        (not block.needs_voice) or (paths.narration / f"bloco_{block.number:03d}.wav").exists()
        for block in plan.blocks
    )


def _autopilot_resolve_chronology(slug: str, catalog: dict[str, Any]) -> None:
    """Infer a soft source chronology only when the author has not locked one."""
    knowledge = load_knowledge(slug)
    if knowledge.chronology_source == "manual":
        append_log(slug, "Autopilot: cronologia manual preservada sem qualquer reordenacao.")
        return
    try:
        settings = load_settings(slug)
        inference, model = infer_footage_chronology(
            catalog.get("videos", []),
            {
                "name": settings.name, "product": settings.product, "objective": settings.objective,
                "locked_facts": knowledge.locked_facts, "notes": knowledge.chronology_notes,
            },
        )
        append_log(
            slug, f"Timeline Resolver com {model}: confidence={inference.confidence:.2f} | {inference.rationale}"
        )
        if inference.confidence >= 0.55:
            knowledge.footage_order = list(inference.ordered_filenames)
            knowledge.chronology_mode = "guided"
            knowledge.chronology_source = "auto"
            save_knowledge(slug, knowledge)
            updated = apply_knowledge_to_catalog(slug, catalog, knowledge)
            if updated:
                write_json(paths_for(slug).analysis / "catalogo_cenas.json", updated)
    except Exception as exc:
        append_log(slug, f"Timeline Resolver indisponivel; ordem existente preservada: {exc}")


def run_autopilot(slug: str) -> None:
    """Autopilot-first v6: truthful progress, self-recovery, and human help only for true hard ambiguity."""
    set_job(slug, "autopilot", status="running", progress=2, message="Autopilot Studio iniciado", error=None)
    paths = paths_for(slug)
    try:
        knowledge = load_knowledge(slug)
        current_footage = [p.name for p in ordered_footage(slug) if p.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS]
        if not current_footage:
            raise RuntimeError("Adicione filmagens em 01_FILMAGENS antes de iniciar o Autopilot")

        catalog = read_json(paths.analysis / "catalogo_cenas.json")
        catalog_names = [v.get("filename", "") for v in (catalog or {}).get("videos", [])]
        analysis_valid = bool(
            catalog and catalog_is_professional(catalog)
            and set(catalog_names) == set(current_footage)
            and len(catalog_names) == len(current_footage)
        )
        analysis_reran = False
        if not analysis_valid:
            set_job(slug, "autopilot", progress=4, message="Assistindo e catalogando as filmagens")
            with mirror_job_progress(slug, "analysis", "autopilot", 4, 22, "Análise das filmagens"):
                run_analysis(slug)
            job = jobs.get(job_key(slug, "analysis"))
            if not job or job.status != "completed":
                raise RuntimeError((job.error if job else None) or "A analise nao foi concluida")
            catalog = read_json(paths.analysis / "catalogo_cenas.json") or {}
            analysis_reran = True
        else:
            set_job(slug, "autopilot", progress=22, message="Análise válida encontrada; reutilizando evidências")
            append_log(slug, "Autopilot: catalogo profissional reutilizado; analise nao foi refeita.")

        set_job(slug, "autopilot", progress=24, message="Resolvendo cronologia provável nos bastidores")
        _autopilot_resolve_chronology(slug, catalog or {})

        raw_plan = read_json(paths.analysis / "roteiro.json")
        if not raw_plan or analysis_reran:
            set_job(slug, "autopilot", progress=28, message="Criando roteiro e plano editorial")
            with mirror_job_progress(slug, "script", "autopilot", 28, 43, "Roteiro"):
                run_script_generation(slug)
            job = jobs.get(job_key(slug, "script"))
            if not job or job.status != "completed":
                raise RuntimeError((job.error if job else None) or "O roteiro nao foi concluido")
            raw_plan = read_json(paths.analysis / "roteiro.json")
        else:
            set_job(slug, "autopilot", progress=43, message="Roteiro existente reutilizado")
        plan = EditPlan.model_validate(raw_plan)

        if not _approved_voice_ready(slug, plan):
            profile = load_voice_profile()
            embedding = Path(profile.embedding_path) if profile.embedding_path else None
            if profile.prepared and embedding and embedding.exists():
                set_job(slug, "autopilot", progress=45, message="Gerando alternativas de voz para o que ainda falta")
                with mirror_job_progress(slug, "voice", "autopilot", 45, 58, "Narração"):
                    run_voice_generation(slug, None, VoiceGenerateRequest(candidates=2, quality="alta"))
                voice_job = jobs.get(job_key(slug, "voice"))
                if not voice_job or voice_job.status != "completed":
                    raise RuntimeError((voice_job.error if voice_job else None) or "A voz nao foi concluida")
            # Never silently publish a synthetic voice that the creator did not approve.
            if not _approved_voice_ready(slug, plan):
                set_job(
                    slug, "autopilot", status="completed", progress=58,
                    message="Pausa inteligente: aprove/grave as falas pendentes e clique Autopilot novamente", error=None,
                )
                return

        set_job(slug, "autopilot", progress=60, message="Voz aprovada encontrada; preparando corte profissional")
        raw_sync = read_json(paths.analysis / "sincronismo_profissional.json")
        sync_valid = bool(
            raw_sync and str(raw_sync.get("version", "")).startswith("6")
            and raw_sync.get("ready") and raw_sync.get("visual_runs")
        )
        if not sync_valid:
            archive_current_result(slug, "Video Factory v6 reconstruindo o corte")
            with mirror_job_progress(slug, "sync", "autopilot", 60, 84, "Sincronismo e continuidade"):
                run_sync(slug)
            sync_job = jobs.get(job_key(slug, "sync"))
            if not sync_job or sync_job.status != "completed":
                raise RuntimeError((sync_job.error if sync_job else None) or "O sincronismo nao foi aprovado")
        else:
            append_log(slug, "Autopilot: sincronismo v6 aprovado reutilizado.")

        knowledge = load_knowledge(slug)
        if knowledge.workflow_mode == "quick_review":
            set_job(
                slug, "autopilot", status="completed", progress=84,
                message="Revisao rapida: corte profissional preparado. Confira e clique Montar quando quiser.", error=None,
            )
            return
        if knowledge.workflow_mode == "director":
            set_job(
                slug, "autopilot", status="completed", progress=84,
                message="Director: mapa de corte preparado para sua revisao detalhada.", error=None,
            )
            return

        set_job(slug, "autopilot", progress=85, message="Renderizando vídeo final otimizado")
        with mirror_job_progress(slug, "assembly", "autopilot", 85, 95, "Render final"):
            run_assembly(slug)
        assembly_job = jobs.get(job_key(slug, "assembly"))
        if not assembly_job or assembly_job.status != "completed":
            raise RuntimeError((assembly_job.error if assembly_job else None) or "A montagem nao foi concluida")

        set_job(slug, "autopilot", progress=96, message="Criando título, descrição, tags e comentário")
        with mirror_job_progress(slug, "publication", "autopilot", 96, 99, "Pacote de publicação"):
            run_publication(slug)
        publication_job = jobs.get(job_key(slug, "publication"))
        if not publication_job or publication_job.status != "completed":
            raise RuntimeError((publication_job.error if publication_job else None) or "O pacote de publicacao nao foi concluido")
        set_job(
            slug, "autopilot", status="completed", progress=100,
            message="Pronto para publicar: video final + pacote de publicacao concluidos", error=None,
        )
    except Exception as error:
        append_log(slug, traceback.format_exc())
        current = jobs.get(job_key(slug, "autopilot"))
        last_progress = max(1, int(current.progress) if current else 1)
        set_job(
            slug,
            "autopilot",
            status="failed",
            progress=last_progress,
            message="Autopilot encontrou uma exceção que exige atenção",
            error=str(error),
        )

def voice_block_status(slug: str, plan: EditPlan | None) -> list[dict[str, Any]]:
    if not plan:
        return []
    paths = paths_for(slug)
    settings = load_settings(slug)
    original_mode = settings.audio_mode == "original"
    result = []
    for block in plan.blocks:
        candidate_dir = paths.narration / "ia" / f"bloco_{block.number:03d}"
        candidates = []
        meta = read_json(candidate_dir / "candidatos.json") or {}
        for item in meta.get("items", []):
            file = candidate_dir / item["filename"]
            if file.exists():
                candidates.append({**item, "url": f"/api/projects/{slug}/voice/candidate/{block.number}/{item['candidate']}"})
        approval = read_json(paths.narration / f"bloco_{block.number:03d}.approved.json") or {}
        approved = paths.narration / f"bloco_{block.number:03d}.wav"
        result.append({
            "number": block.number,
            "title": block.title,
            "narration": block.narration,
            "needs_voice": (block.needs_voice and not original_mode),
            "audio_mode": settings.audio_mode,
            "approved": approved.exists() if (block.needs_voice and not original_mode) else True,
            "approved_source": approval.get("source", "") if approved.exists() else "",
            "approved_candidate": approval.get("candidate") if approved.exists() else None,
            "approved_version": approved.stat().st_mtime_ns if approved.exists() else 0,
            "approved_url": f"/api/projects/{slug}/voice/approved/{block.number}" if approved.exists() else "",
            "candidates": candidates,
        })
    return result


@app.get("/")
def home() -> FileResponse:
    return FileResponse(
        PACKAGE_ROOT / "app" / "static" / "index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/api/system")
def system_status() -> dict[str, Any]:
    return {
        "app_name": APP_NAME,
        "version": APP_VERSION,
        "api_key_configured": bool(GEMINI_API_KEY),
        "model": GEMINI_MODEL,
        "ffmpeg": executable_exists(FFMPEG_BIN),
        "ffprobe": executable_exists(FFPROBE_BIN),
        "voice": voice_health(),
    }




def apply_knowledge_to_catalog(slug: str, catalog: dict[str, Any] | None, knowledge: ProjectKnowledge) -> dict[str, Any] | None:
    if not catalog:
        return catalog
    order = {name: idx for idx, name in enumerate(knowledge.footage_order, start=1)}
    videos = catalog.get("videos", [])
    for video in videos:
        video["sequence_index"] = order.get(video.get("filename", ""), len(order) + 1)
    videos.sort(key=lambda v: (int(v.get("sequence_index", 999999)), str(v.get("filename", "")).casefold()))
    catalog["videos"] = videos
    catalog["knowledge"] = knowledge.model_dump(mode="json")
    return catalog


def _parse_recorded_at(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        normalized = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def run_evidence_search(slug: str, request: DirectedEvidenceSearchRequest) -> None:
    set_job(slug, "evidence_search", status="running", progress=2, message="Preparando busca dirigida", error=None)
    paths = paths_for(slug)
    try:
        catalog = read_json(paths.analysis / "catalogo_cenas.json")
        if not catalog:
            raise RuntimeError("Analise as filmagens antes da busca dirigida")
        set_job(slug, "evidence_search", progress=12, message=f"Procurando: {request.target[:90]}")
        result = search_directed_evidence(
            paths, catalog, request.target, load_prompt("buscar_evidencia_dirigida.txt"),
            logger=lambda msg: append_log(slug, msg),
            filenames=request.filenames, max_candidates=request.max_candidates,
            progress=lambda pct, msg: set_job(
                slug, "evidence_search",
                progress=min(96, 12 + int(max(0, min(100, pct)) * 0.84)),
                message=msg,
            ),
        )
        set_job(
            slug, "evidence_search", status="completed", progress=100,
            message=f"Busca concluida: {len(result.candidates)} candidato(s) confirmado(s)", error=None,
        )
    except Exception as error:
        append_log(slug, traceback.format_exc())
        set_job(slug, "evidence_search", status="failed", progress=0, message="Falha na busca dirigida", error=str(error))


@app.get("/api/projects")
def api_list_projects() -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in list_projects()]


@app.post("/api/projects")
def api_create_project(payload: ProjectCreate) -> dict[str, Any]:
    return create_project(payload).model_dump(mode="json")


@app.get("/api/projects/{slug}")
def api_get_project(slug: str) -> dict[str, Any]:
    try:
        settings = load_settings(slug)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    paths = paths_for(slug)
    raw_plan = read_json(paths.analysis / "roteiro.json")
    plan = EditPlan.model_validate(raw_plan) if raw_plan else None
    knowledge = load_knowledge(slug)
    catalog = apply_knowledge_to_catalog(slug, read_json(paths.analysis / "catalogo_cenas.json"), knowledge)
    if catalog:
        write_json(paths.analysis / "catalogo_cenas.json", catalog)
    raw_sync = read_json(paths.analysis / "sincronismo_profissional.json")
    publication = read_json(paths.publication / "PUBLICACAO.json")
    result = paths.result / "CONTEUDO_PRINCIPAL.mp4"
    master = paths.result / "MASTER_DRSAYGON_VIDEO_FACTORY.mp4"
    delivery_exists = result.exists()
    master_exists = master.exists()
    primary = result if delivery_exists else master
    primary_exists = delivery_exists or master_exists
    notes_path = paths.information / "observacoes.txt"
    evidence = flatten_evidence(catalog or {}) if catalog else []
    return {
        "settings": settings.model_dump(mode="json"),
        "paths": {key: str(value) for key, value in paths.model_dump().items()},
        "footage": knowledge.footage_order,
        "knowledge": knowledge.model_dump(mode="json"),
        "catalog": catalog,
        "analysis_professional": catalog_is_professional(catalog or {}) if catalog else False,
        "plan": raw_plan,
        "voice_blocks": voice_block_status(slug, plan),
        "sync": raw_sync,
        "evidence_options": [
            {
                "event_id": item["event_id"],
                "filename": item["filename"],
                "start_seconds": item["start_seconds"],
                "end_seconds": item["end_seconds"],
                "description": item["description"],
            } for item in evidence
        ],
        "publication": publication,
        # Backward-compatible result_* fields now refer to the preferred preview:
        # publication file when available, otherwise the Master.
        "result_exists": primary_exists,
        "delivery_exists": delivery_exists,
        "result_stale": primary_exists and (
            not bool(raw_sync) or not bool(raw_sync.get("ready", False))
            or not str((raw_sync or {}).get("version", "")).startswith("6")
            or not bool((raw_sync or {}).get("visual_runs"))
        ),
        "result_path": str(primary),
        "delivery_path": str(result),
        "master_exists": master_exists,
        "master_path": str(master),
        "master_revision": int(master.stat().st_mtime_ns) if master_exists else 0,
        "delivery_revision": int(result.stat().st_mtime_ns) if delivery_exists else 0,
        "result_revision": int(primary.stat().st_mtime_ns) if primary_exists else 0,
        "notes": notes_path.read_text(encoding="utf-8") if notes_path.exists() else "",
        "directed_evidence": read_json(paths.analysis / "busca_dirigida_evidencias.json"),
    }


@app.get("/api/projects/{slug}/chronology/suggestion")
def api_chronology_suggestion(slug: str) -> dict[str, Any]:
    """Suggest source chronology from embedded recording timestamps, never silently apply it."""
    load_settings(slug)
    paths = paths_for(slug)
    knowledge = load_knowledge(slug)
    current_order = list(knowledge.footage_order)
    hints: list[dict[str, Any]] = []
    valid: list[tuple[datetime, str]] = []
    for filename in current_order:
        source = paths.footage / filename
        if not source.exists() or source.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
            continue
        recorded_at = ""
        try:
            recorded_at = probe_media(source).recorded_at
        except Exception as exc:
            append_log(slug, f"Nao foi possivel ler data de gravacao de {filename}: {exc}")
        parsed = _parse_recorded_at(recorded_at)
        hints.append({
            "filename": filename,
            "recorded_at": recorded_at,
            "usable": parsed is not None,
        })
        if parsed is not None:
            valid.append((parsed, filename))

    if len(valid) < 2:
        return {
            "suggested_order": current_order, "hints": hints, "usable_count": len(valid),
            "confidence": "unavailable",
            "message": "Poucos arquivos possuem data de gravacao embutida. Mantenha a ordem manual.",
        }
    valid.sort(key=lambda pair: pair[0])
    sorted_names = [filename for _, filename in valid]
    missing = [name for name in current_order if name not in set(sorted_names)]
    suggested = sorted_names + missing
    confidence = "high" if len(valid) == len(current_order) else "partial"
    return {
        "suggested_order": suggested, "hints": hints, "usable_count": len(valid),
        "confidence": confidence,
        "message": (
            "Ordem sugerida pela data/hora de gravacao embutida nos videos. Confirme visualmente antes de salvar."
            if confidence == "high" else
            "Sugestao parcial: alguns arquivos nao possuem data de gravacao embutida. Confirme/reordene manualmente."
        ),
    }


@app.put("/api/projects/{slug}/settings")
def api_save_project_settings(slug: str, payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings(slug)
    previous_audio = settings.audio_mode
    previous_master = settings.create_master
    previous_master_profile = settings.master_profile
    previous_create_delivery = settings.create_delivery
    previous_delivery = settings.delivery_profile
    if "audio_mode" in payload:
        value = str(payload.get("audio_mode", "")).strip()
        if value not in {"narration", "original", "mixed"}:
            raise HTTPException(status_code=400, detail="Modo de audio invalido")
        settings.audio_mode = value
    if "create_master" in payload:
        settings.create_master = bool(payload.get("create_master"))
    if "master_profile" in payload:
        value = str(payload.get("master_profile", "")).strip()
        if value not in {"premiere_hq", "premiere_max"}:
            raise HTTPException(status_code=400, detail="Perfil Master invalido")
        settings.master_profile = value
    if "create_delivery" in payload:
        settings.create_delivery = bool(payload.get("create_delivery"))
    if "delivery_profile" in payload:
        value = str(payload.get("delivery_profile", "")).strip()
        if value not in {"youtube_1080p", "social_1080p"}:
            raise HTTPException(status_code=400, detail="Perfil de publicacao invalido")
        settings.delivery_profile = value
    if not settings.create_master and not settings.create_delivery:
        raise HTTPException(status_code=400, detail="Selecione pelo menos uma saída: Master ou arquivo de publicação")
    save_settings(slug, settings)
    if settings.audio_mode != previous_audio:
        invalidate_sync(slug, f"modo de audio alterado para {settings.audio_mode}")
    elif (
        settings.create_master != previous_master
        or settings.master_profile != previous_master_profile
        or settings.create_delivery != previous_create_delivery
        or settings.delivery_profile != previous_delivery
    ):
        archive_current_result(slug, "configuracao de exportacao alterada")
    return {"ok": True, "settings": settings.model_dump(mode="json")}


@app.put("/api/projects/{slug}/knowledge")
def api_save_knowledge(slug: str, payload: ProjectKnowledge) -> dict[str, Any]:
    load_settings(slug)
    previous = load_knowledge(slug)
    knowledge = save_knowledge(slug, payload)
    paths = paths_for(slug)
    catalog = apply_knowledge_to_catalog(slug, read_json(paths.analysis / "catalogo_cenas.json"), knowledge)
    if catalog:
        write_json(paths.analysis / "catalogo_cenas.json", catalog)
    semantic_fields = ("editing_style", "locked_facts", "chronology_mode", "footage_order", "chronology_notes")
    changed = any(getattr(previous, field) != getattr(knowledge, field) for field in semantic_fields)
    if changed:
        invalidate_sync(slug, "ordem cronologica/fatos confirmados alterados")
    return {"ok": True, "knowledge": knowledge.model_dump(mode="json")}


@app.post("/api/projects/{slug}/evidence/search")
def api_search_evidence(slug: str, request: DirectedEvidenceSearchRequest) -> dict[str, str]:
    load_settings(slug)
    current = jobs.get(job_key(slug, "evidence_search"))
    if current and current.status in {"queued", "running"}:
        return {"status": current.status}
    set_job(slug, "evidence_search", status="queued", progress=0, message="Busca dirigida na fila", error=None)
    executor.submit(run_evidence_search, slug, request)
    return {"status": "queued"}


@app.get("/api/projects/{slug}/evidence/search/preview/{candidate_index}")
def api_directed_preview(slug: str, candidate_index: int) -> FileResponse:
    paths = paths_for(slug)
    raw = read_json(paths.analysis / "busca_dirigida_evidencias.json")
    if not raw:
        raise HTTPException(status_code=404, detail="Nenhuma busca dirigida disponivel")
    items = raw.get("candidates", [])
    if candidate_index < 0 or candidate_index >= len(items):
        raise HTTPException(status_code=404, detail="Candidato inexistente")
    item = items[candidate_index]
    source = paths.footage / item["filename"]
    if not source.exists():
        raise HTTPException(status_code=404, detail="Filmagem original nao encontrada")
    preview = paths.temp / "directed_evidence" / f"candidate_preview_{candidate_index:03d}.mp4"
    create_verification_proxy(source, preview, float(item["source_start_seconds"]), float(item["source_end_seconds"]))
    return FileResponse(preview, media_type="video/mp4", filename=preview.name)


@app.post("/api/projects/{slug}/evidence/search/accept/{candidate_index}")
def api_accept_directed_evidence(slug: str, candidate_index: int) -> dict[str, Any]:
    paths = paths_for(slug)
    raw = read_json(paths.analysis / "busca_dirigida_evidencias.json")
    catalog = read_json(paths.analysis / "catalogo_cenas.json")
    if not raw or not catalog:
        raise HTTPException(status_code=400, detail="Busca ou catalogo ausente")
    items = raw.get("candidates", [])
    if candidate_index < 0 or candidate_index >= len(items):
        raise HTTPException(status_code=404, detail="Candidato inexistente")
    item = items[candidate_index]
    payload = ManualEvidenceRequest(
        filename=item["filename"],
        start_seconds=float(item["source_start_seconds"]),
        end_seconds=float(item["source_end_seconds"]),
        label=item.get("observed_evidence") or raw.get("target", "Evidência confirmada"),
        event_kind="phenomenon" if item.get("visual_phenomena") else "action",
        actions=item.get("actions", []), states=item.get("states", []),
        visual_phenomena=item.get("visual_phenomena", []) or [raw.get("target", "evidência visual")],
        critical_action=True,
    )
    segment = add_manual_evidence(paths, catalog, payload)
    invalidate_sync(slug, "evidencia dirigida confirmada pelo autor")
    return {"ok": True, "segment": segment}


@app.post("/api/projects/{slug}/evidence/manual")
def api_add_manual_evidence(slug: str, payload: ManualEvidenceRequest) -> dict[str, Any]:
    paths = paths_for(slug)
    catalog = read_json(paths.analysis / "catalogo_cenas.json")
    if not catalog:
        raise HTTPException(status_code=400, detail="Analise as filmagens primeiro")
    try:
        segment = add_manual_evidence(paths, catalog, payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    invalidate_sync(slug, "evidencia marcada manualmente")
    return {"ok": True, "segment": segment}


@app.get("/api/projects/{slug}/footage/{filename}")
def api_project_footage(slug: str, filename: str) -> FileResponse:
    paths = paths_for(slug)
    safe = Path(filename).name
    if safe != filename:
        raise HTTPException(status_code=400, detail="Nome de arquivo invalido")
    source = paths.footage / safe
    if not source.exists() or source.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=404, detail="Filmagem nao encontrada")
    media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    return FileResponse(source, media_type=media_type, filename=source.name)


@app.get("/api/projects/{slug}/footage-preview/{filename}")
def api_project_footage_preview(slug: str, filename: str) -> FileResponse:
    """Serve a browser-safe H.264/AAC proxy for the Evidence Lab player.

    Camera originals may be HEVC, 10-bit, variable-frame-rate or otherwise
    unsupported by a browser even though FFmpeg can analyze them. Analysis already
    creates a lightweight H.264/AAC proxy, so the manual player must use that same
    compatible asset instead of gambling on the original codec. FileResponse in
    current Starlette also honors byte-range requests, which gives reliable seek.
    """
    paths = paths_for(slug)
    safe = Path(filename).name
    if safe != filename:
        raise HTTPException(status_code=400, detail="Nome de arquivo invalido")
    source = paths.footage / safe
    if not source.exists() or source.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=404, detail="Filmagem nao encontrada")

    catalog = read_json(paths.analysis / "catalogo_cenas.json") or {}
    video = next((item for item in catalog.get("videos", []) if item.get("filename") == safe), None)
    sequence_index = int(video.get("sequence_index", 0)) if video else 0
    if sequence_index > 0:
        preview = paths.temp / "proxies" / f"proxy_{sequence_index:03d}.mp4"
    else:
        # Defensive fallback for projects opened before analysis completed.
        preview = paths.temp / "browser_preview" / f"{source.stem}_browser.mp4"

    if not preview.exists() or preview.stat().st_mtime < source.stat().st_mtime:
        create_proxy(source, preview)

    return FileResponse(
        preview,
        media_type="video/mp4",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.post("/api/projects/{slug}/notes")
def api_save_notes(slug: str, payload: dict[str, str]) -> dict[str, bool]:
    load_settings(slug)
    paths_for(slug).information.joinpath("observacoes.txt").write_text(payload.get("notes", ""), encoding="utf-8")
    return {"ok": True}


@app.post("/api/projects/{slug}/open/{folder}")
def api_open_folder(slug: str, folder: str) -> dict[str, bool]:
    paths = paths_for(slug)
    allowed = {
        "footage": paths.footage, "information": paths.information, "analysis": paths.analysis,
        "narration": paths.narration, "result": paths.result, "publication": paths.publication,
        "root": paths.root,
    }
    if folder not in allowed:
        raise HTTPException(status_code=400, detail="Pasta invalida")
    open_folder(allowed[folder])
    return {"ok": True}


@app.post("/api/projects/{slug}/autopilot")
def api_autopilot(slug: str) -> dict[str, str]:
    load_settings(slug)
    current = jobs.get(job_key(slug, "autopilot"))
    if current and current.status in {"queued", "running"}:
        return {"status": current.status}
    if any((jobs.get(job_key(slug, name)) and jobs[job_key(slug, name)].status in {"queued", "running"}) for name in ("analysis", "script", "voice", "sync", "assembly")):
        raise HTTPException(status_code=409, detail="Ha outra tarefa do projeto em andamento")
    set_job(slug, "autopilot", status="queued", progress=0, message="Piloto automatico na fila", error=None)
    executor.submit(run_autopilot, slug)
    return {"status": "queued"}


@app.post("/api/projects/{slug}/analyze")
def api_analyze(slug: str) -> dict[str, str]:
    load_settings(slug)
    current = jobs.get(job_key(slug, "analysis"))
    if current and current.status in {"queued", "running"}:
        return {"status": current.status}
    set_job(slug, "analysis", status="queued", progress=0, message="Analise na fila", error=None)
    executor.submit(run_analysis, slug)
    return {"status": "queued"}


@app.post("/api/projects/{slug}/generate-script")
def api_generate_script(slug: str) -> dict[str, str]:
    load_settings(slug)
    current = jobs.get(job_key(slug, "script"))
    if current and current.status in {"queued", "running"}:
        return {"status": current.status}
    set_job(slug, "script", status="queued", progress=0, message="Roteiro na fila", error=None)
    executor.submit(run_script_generation, slug)
    return {"status": "queued"}


@app.put("/api/projects/{slug}/plan")
def api_save_plan(slug: str, payload: dict[str, Any]) -> dict[str, bool]:
    settings = load_settings(slug)
    paths = paths_for(slug)
    catalog = read_json(paths.analysis / "catalogo_cenas.json")
    if not catalog:
        raise HTTPException(status_code=400, detail="Catalogo de cenas ausente")
    old_data = read_json(paths.analysis / "roteiro.json")
    old = EditPlan.model_validate(old_data) if old_data else None
    plan = sanitize_plan(EditPlan.model_validate(payload), catalog, settings.target_duration_seconds)
    _invalidate_voice_for_changed_blocks(slug, old, plan)
    write_json(paths.analysis / "roteiro.json", plan.model_dump(mode="json"))
    invalidate_sync(slug, "texto do roteiro alterado")
    return {"ok": True}


@app.get("/api/voice/profile")
def api_voice_profile() -> dict[str, Any]:
    profile = load_voice_profile()
    if not profile.pronunciation_rules:
        profile.pronunciation_rules = default_pronunciations()
        save_voice_profile(profile)
    return {"profile": profile.model_dump(mode="json"), "engine": voice_health()}


@app.put("/api/voice/pronunciations")
def api_voice_pronunciations(payload: dict[str, str]) -> dict[str, Any]:
    profile = load_voice_profile()
    rules: dict[str, str] = {}
    for line in payload.get("text", "").splitlines():
        if "=" not in line:
            continue
        source, replacement = line.split("=", 1)
        if source.strip() and replacement.strip():
            rules[source.strip()] = replacement.strip()
    profile.pronunciation_rules = rules
    save_voice_profile(profile)
    return {"ok": True, "rules": rules}


@app.post("/api/voice/profile")
async def api_prepare_voice_profile(audio: UploadFile = File(...)) -> dict[str, Any]:
    extension = Path(audio.filename or "voz.wav").suffix.lower()
    if extension not in SUPPORTED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Formato de audio nao suportado")
    root = profile_root()
    original = root / f"reference_original{extension}"
    for old in root.glob("reference_original.*"):
        old.unlink(missing_ok=True)
    with original.open("wb") as handle:
        shutil.copyfileobj(audio.file, handle)
    clean = root / "reference_clean.wav"
    embedding = root / "voice.safetensors"
    prepare_voice_reference(original, clean)
    try:
        export_voice(clean, embedding)
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    profile = load_voice_profile()
    profile.reference_original = str(original)
    profile.reference_clean = str(clean)
    profile.embedding_path = str(embedding)
    profile.prepared = embedding.exists()
    if not profile.pronunciation_rules:
        profile.pronunciation_rules = default_pronunciations()
    save_voice_profile(profile)
    return {"ok": True, "profile": profile.model_dump(mode="json")}


@app.get("/api/voice/reference")
def api_voice_reference() -> FileResponse:
    profile = load_voice_profile()
    path = Path(profile.reference_clean) if profile.reference_clean else None
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Amostra de voz ainda nao configurada")
    return FileResponse(path, media_type="audio/wav", filename="minha_voz_referencia.wav")


@app.post("/api/projects/{slug}/voice/generate-all")
def api_generate_all_voice(slug: str, request: VoiceGenerateRequest) -> dict[str, str]:
    load_settings(slug)
    current = jobs.get(job_key(slug, "voice"))
    if current and current.status in {"queued", "running"}:
        return {"status": current.status}
    set_job(slug, "voice", status="queued", progress=0, message="Voz na fila", error=None)
    executor.submit(run_voice_generation, slug, None, request)
    return {"status": "queued"}


@app.post("/api/projects/{slug}/voice/generate/{block_number}")
def api_generate_block_voice(slug: str, block_number: int, request: VoiceGenerateRequest) -> dict[str, str]:
    load_settings(slug)
    current = jobs.get(job_key(slug, "voice"))
    if current and current.status in {"queued", "running"}:
        return {"status": current.status}
    set_job(slug, "voice", status="queued", progress=0, message="Voz na fila", error=None)
    executor.submit(run_voice_generation, slug, block_number, request)
    return {"status": "queued"}


@app.get("/api/projects/{slug}/voice/candidate/{block_number}/{candidate}")
def api_voice_candidate(slug: str, block_number: int, candidate: int) -> FileResponse:
    path = paths_for(slug).narration / "ia" / f"bloco_{block_number:03d}" / f"candidato_{candidate:02d}.wav"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Candidato nao encontrado")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@app.post("/api/projects/{slug}/voice/approve/{block_number}/{candidate}")
def api_approve_voice(slug: str, block_number: int, candidate: int) -> dict[str, Any]:
    paths = paths_for(slug)
    source = paths.narration / "ia" / f"bloco_{block_number:03d}" / f"candidato_{candidate:02d}.wav"
    if not source.exists():
        raise HTTPException(status_code=404, detail="Candidato nao encontrado")
    target = paths.narration / f"bloco_{block_number:03d}.wav"
    shutil.copy2(source, target)
    write_json(paths.narration / f"bloco_{block_number:03d}.approved.json", {
        "source": "ia", "candidate": candidate, "approved_at": now_iso()
    })
    invalidate_sync(slug, f"voz aprovada alterada no bloco {block_number}")
    return {"ok": True, "filename": target.name}


@app.get("/api/projects/{slug}/voice/approved/{block_number}")
def api_approved_voice(slug: str, block_number: int) -> FileResponse:
    path = paths_for(slug).narration / f"bloco_{block_number:03d}.wav"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Voz ainda nao aprovada")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@app.post("/api/projects/{slug}/recording")
async def api_save_manual_recording(
    slug: str,
    block_number: int = Form(...),
    audio: UploadFile = File(...),
) -> dict[str, Any]:
    load_settings(slug)
    paths = paths_for(slug)
    extension = Path(audio.filename or "recording.webm").suffix.lower() or ".webm"
    raw = paths.temp / "manual_voice" / f"bloco_{block_number:03d}{extension}"
    raw.parent.mkdir(parents=True, exist_ok=True)
    with raw.open("wb") as handle:
        shutil.copyfileobj(audio.file, handle)
    target = paths.narration / f"bloco_{block_number:03d}.wav"
    bounds = safe_speech_bounds(raw)
    trim_start, trim_end, raw_duration = bounds
    convert_recording_to_wav(raw, target, bounds=bounds)
    duration_seconds = probe_audio_duration(target)
    if duration_seconds < 0.5:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="A gravacao ficou curta demais. Grave o bloco novamente.")
    write_json(paths.narration / f"bloco_{block_number:03d}.approved.json", {
        "source": "manual", "approved_at": now_iso(), "duration_seconds": round(duration_seconds, 3)
    })
    invalidate_sync(slug, f"gravacao manual alterada no bloco {block_number}")
    trimmed = trim_start > 0.001 or (raw_duration > 0 and trim_end < raw_duration - 0.001)
    append_log(
        slug,
        f"Narracao manual aprovada: {target.name} ({duration_seconds:.2f}s; "
        f"edge-safe trim={trimmed}; inicio={trim_start:.3f}s; fim={trim_end:.3f}s; "
        "margens de seguranca preservadas)",
    )
    return {
        "ok": True,
        "filename": target.name,
        "bytes": target.stat().st_size,
        "duration_seconds": round(duration_seconds, 3),
        "edge_silence_trimmed": trimmed,
        "edge_trim_start_seconds": round(trim_start, 3),
        "edge_trim_end_seconds": round(trim_end, 3),
        "raw_duration_seconds": round(raw_duration, 3),
        "edge_policy": "safe_silence_trim_with_speech_guards",
    }


@app.delete("/api/projects/{slug}/recording/{block_number}")
def api_delete_recording(slug: str, block_number: int) -> dict[str, bool]:
    paths = paths_for(slug)
    (paths.narration / f"bloco_{block_number:03d}.wav").unlink(missing_ok=True)
    (paths.narration / f"bloco_{block_number:03d}.approved.json").unlink(missing_ok=True)
    invalidate_sync(slug, f"narracao descartada no bloco {block_number}")
    return {"ok": True}


@app.post("/api/projects/{slug}/sync/build")
def api_build_sync(slug: str) -> dict[str, str]:
    load_settings(slug)
    current = jobs.get(job_key(slug, "sync"))
    if current and current.status in {"queued", "running"}:
        return {"status": current.status}
    for name in ("analysis", "script", "voice", "assembly"):
        other = jobs.get(job_key(slug, name))
        if other and other.status in {"queued", "running"}:
            raise HTTPException(status_code=409, detail=f"A tarefa '{name}' ainda esta em andamento")
    # Any render produced from a previous map is no longer current once a new
    # synchronization pass is requested. Preserve it in history.
    archive_current_result(slug, "novo sincronismo solicitado")
    set_job(slug, "sync", status="queued", progress=0, message="Sincronismo na fila", error=None)
    executor.submit(run_sync, slug)
    return {"status": "queued"}


@app.put("/api/projects/{slug}/sync/beat/{beat_id}")
def api_override_sync_beat(slug: str, beat_id: str, payload: dict[str, str]) -> dict[str, Any]:
    paths = paths_for(slug)
    raw_sync = read_json(paths.analysis / "sincronismo_profissional.json")
    catalog = read_json(paths.analysis / "catalogo_cenas.json")
    if not raw_sync or not catalog:
        raise HTTPException(status_code=400, detail="Gere o sincronismo antes de alterar uma tomada")
    event_id = payload.get("event_id", "").strip()
    if not event_id:
        raise HTTPException(status_code=400, detail="event_id obrigatorio")
    archive_current_result(slug, f"tomada manual alterada em {beat_id}")
    try:
        sync = apply_manual_event_override(
            paths, ProfessionalSyncPlan.model_validate(raw_sync), catalog, beat_id, event_id
        )
    except ProfessionalSyncError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    # The human choice is authoritative for this beat, but changing one shot can
    # alter timing/chronology in the assembled edit. Re-run only the cheap global
    # QA instead of recalculating the whole semantic map and losing the override.
    set_job(slug, "sync", status="queued", progress=84, message="Tomada aplicada; QA global na fila", error=None)
    executor.submit(run_sync_review_only, slug)
    return {"ok": True, "ready": False, "reviewing": True}


@app.put("/api/projects/{slug}/sync/run/{run_id}/audio")
def api_override_run_audio(slug: str, run_id: str, payload: dict[str, str]) -> dict[str, Any]:
    paths = paths_for(slug)
    raw_sync = read_json(paths.analysis / "sincronismo_profissional.json")
    if not raw_sync:
        raise HTTPException(status_code=400, detail="Prepare o sincronismo antes de configurar o audio da cena")
    sync = ProfessionalSyncPlan.model_validate(raw_sync)
    strategy = str(payload.get("audio_strategy", "")).strip()
    if strategy not in {"mute", "ambient", "feature"}:
        raise HTTPException(status_code=400, detail="Estrategia de audio invalida")
    run = next((item for item in sync.visual_runs if item.run_id == run_id), None)
    if not run:
        raise HTTPException(status_code=404, detail="Tomada nao encontrada")
    run.audio_strategy = strategy
    sync.version = "6.0"
    write_json(paths.analysis / "sincronismo_profissional.json", sync.model_dump(mode="json"))
    archive_current_result(slug, f"audio da tomada {run_id} alterado para {strategy}")
    append_log(slug, f"Audio da tomada {run_id}: {strategy}")
    return {"ok": True, "run_id": run_id, "audio_strategy": strategy}


@app.get("/api/projects/{slug}/sync/preview/{beat_id}")
def api_sync_preview(slug: str, beat_id: str) -> FileResponse:
    paths = paths_for(slug)
    raw_sync = read_json(paths.analysis / "sincronismo_profissional.json")
    if not raw_sync:
        raise HTTPException(status_code=404, detail="Sincronismo ainda nao gerado")
    sync = ProfessionalSyncPlan.model_validate(raw_sync)
    beat = next((item for item in sync.beats if item.beat_id == beat_id), None)
    if not beat or not beat.filename or beat.verification == "failed":
        raise HTTPException(status_code=404, detail="Esta batida ainda nao possui tomada valida")
    preview_dir = paths.temp / "sync_previews"
    preview = preview_dir / f"{beat_id}.mp4"
    source = paths.footage / beat.filename
    if (
        not preview.exists()
        or preview.stat().st_mtime < source.stat().st_mtime
        or preview.stat().st_mtime < (paths.analysis / "sincronismo_profissional.json").stat().st_mtime
    ):
        create_verification_proxy(source, preview, beat.source_start_seconds, beat.source_end_seconds)
    return FileResponse(preview, media_type="video/mp4", filename=f"{beat_id}.mp4")


@app.get("/api/projects/{slug}/sync/run/{run_id}")
def api_sync_run_preview(slug: str, run_id: str) -> FileResponse:
    paths = paths_for(slug)
    raw_sync = read_json(paths.analysis / "sincronismo_profissional.json")
    if not raw_sync:
        raise HTTPException(status_code=404, detail="Sincronismo ainda nao gerado")
    sync = ProfessionalSyncPlan.model_validate(raw_sync)
    run = next((item for item in sync.visual_runs if item.run_id == run_id), None)
    if not run or not run.filename or run.verification == "failed":
        raise HTTPException(status_code=404, detail="Esta tomada ainda nao possui evidencia valida")
    preview_dir = paths.temp / "run_previews"
    preview = preview_dir / f"{run_id}.mp4"
    source = paths.footage / run.filename
    if (
        not preview.exists()
        or preview.stat().st_mtime < source.stat().st_mtime
        or preview.stat().st_mtime < (paths.analysis / "sincronismo_profissional.json").stat().st_mtime
    ):
        create_verification_proxy(source, preview, run.source_start_seconds, run.source_end_seconds)
    return FileResponse(preview, media_type="video/mp4", filename=f"{run_id}.mp4")


@app.post("/api/projects/{slug}/assemble")
def api_assemble(slug: str) -> dict[str, str]:
    load_settings(slug)
    current = jobs.get(job_key(slug, "assembly"))
    if current and current.status in {"queued", "running"}:
        return {"status": current.status}
    for name in ("analysis", "script", "voice", "sync"):
        other = jobs.get(job_key(slug, name))
        if other and other.status in {"queued", "running"}:
            raise HTTPException(status_code=409, detail=f"A tarefa '{name}' ainda esta em andamento")
    set_job(slug, "assembly", status="queued", progress=0, message="Montagem na fila", error=None)
    executor.submit(run_assembly, slug)
    return {"status": "queued"}


@app.post("/api/projects/{slug}/publication")
def api_generate_publication(slug: str) -> dict[str, str]:
    load_settings(slug)
    current = jobs.get(job_key(slug, "publication"))
    if current and current.status in {"queued", "running"}:
        return {"status": current.status}
    set_job(slug, "publication", status="queued", progress=0, message="Publicacao na fila", error=None)
    executor.submit(run_publication, slug)
    return {"status": "queued"}


@app.get("/api/projects/{slug}/jobs")
def api_jobs(slug: str) -> dict[str, Any]:
    return {
        name: (jobs.get(job_key(slug, name)) or JobState(project_slug=slug, job=name)).model_dump(mode="json")
        for name in JOB_NAMES
    }


@app.get("/api/projects/{slug}/logs")
def api_logs(slug: str) -> dict[str, str]:
    path = paths_for(slug).logs / "atividade.log"
    if not path.exists():
        return {"logs": ""}
    return {"logs": path.read_text(encoding="utf-8", errors="replace")[-40000:]}


@app.get("/api/projects/{slug}/result/master")
def api_master_result(slug: str) -> FileResponse:
    path = paths_for(slug).result / "MASTER_DRSAYGON_VIDEO_FACTORY.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="O arquivo mestre ainda nao foi gerado")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/api/projects/{slug}/result/delivery")
def api_delivery_result(slug: str) -> FileResponse:
    path = paths_for(slug).result / "CONTEUDO_PRINCIPAL.mp4"
    if not path.exists():
        raise HTTPException(status_code=404, detail="O arquivo de publicação ainda nao foi gerado")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/api/projects/{slug}/result")
def api_result(slug: str) -> FileResponse:
    paths = paths_for(slug)
    delivery = paths.result / "CONTEUDO_PRINCIPAL.mp4"
    master = paths.result / "MASTER_DRSAYGON_VIDEO_FACTORY.mp4"
    path = delivery if delivery.exists() else master
    if not path.exists():
        raise HTTPException(status_code=404, detail="O video ainda nao foi montado")
    return FileResponse(path, media_type="video/mp4", filename=path.name)
