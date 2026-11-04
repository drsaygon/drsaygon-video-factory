from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from app.models import EditPlan, ProfessionalSyncPlan, ProjectSettings
from app.services.ffmpeg import (
    audio_duration,
    concat_videos,
    mix_narration,
    normalize_voice_audio,
    probe_media,
    render_clip,
    render_punch_in_composite_clip,
    export_delivery,
    export_master,
)
from app.services.continuity import (
    auto_heal_scene_duration_mismatch,
    _tighten_narration_audio,
)
from app.services.project import ProjectPaths, write_json


class AssemblyError(RuntimeError):
    pass


def approved_recording(paths: ProjectPaths, block_number: int) -> Path:
    target = paths.narration / f"bloco_{block_number:03d}.wav"
    if target.exists():
        return target
    raise AssemblyError(
        f"A voz do bloco {block_number} ainda nao foi aprovada. "
        "Va para a etapa Narracao e aprove uma geracao ou grave manualmente."
    )


def _block_sync_beats(sync: ProfessionalSyncPlan, block_number: int):
    return sorted(
        [beat for beat in sync.beats if beat.block_number == block_number],
        key=lambda beat: (beat.output_start_seconds, beat.beat_number),
    )


def _block_visual_runs(sync: ProfessionalSyncPlan, block_number: int):
    return sorted(
        [run for run in sync.visual_runs if run.block_number == block_number],
        key=lambda run: run.output_start_seconds,
    )


def assemble_project(
    paths: ProjectPaths,
    settings: ProjectSettings,
    plan: EditPlan,
    sync: ProfessionalSyncPlan,
    logger: Callable[[str], None],
    progress: Callable[[int, str], None],
) -> Path:
    """Assemble from v6 visual runs.

    Semantic beats prove what must be visible. Visual runs decide where an editor
    would actually cut. In v6, long-form editing is continuity-first: a sentence
    boundary is never, by itself, a reason to cut.
    """
    if not plan.blocks:
        raise AssemblyError("O roteiro nao possui blocos")
    if not sync.ready:
        failed = [b for b in sync.beats if b.verification == "failed"]
        preview = "; ".join(b.text[:90] for b in failed[:3])
        raise AssemblyError(
            "O sincronismo profissional ainda possui batidas sem evidencia valida. "
            f"Corrija/reprocesse antes da montagem. {preview}"
        )

    width, height = (1080, 1920) if settings.format == "short_9_16" else (1920, 1080)
    if not sync.visual_runs:
        raise AssemblyError(
            "Este sincronismo ainda e da arquitetura anterior. Execute Preparar sincronismo "
            "para o Scene Continuity Engine v6 criar as tomadas reais antes da montagem."
        )
    working = paths.temp / "assembly_v6"
    if working.exists():
        shutil.rmtree(working)
    working.mkdir(parents=True, exist_ok=True)

    block_outputs: list[Path] = []
    report: dict[str, object] = {
        "engine": "DrSaygon Video Factory v6.0 / Scene Continuity Engine / Audio Mode Engine / Master Delivery",
        "format": settings.format,
        "resolution": f"{width}x{height}",
        "sync_generated_at": sync.generated_at,
        "blocks": [],
        "warnings": list(sync.warnings),
        "edit_quality": sync.edit_quality.model_dump(mode="json") if sync.edit_quality else None,
        "visual_run_count": len(sync.visual_runs),
        "semantic_beat_count": len(sync.beats),
    }

    for block_index, block in enumerate(plan.blocks, start=1):
        percent_base = int((block_index - 1) / len(plan.blocks) * 88)
        progress(percent_base, f"Montando bloco {block.number}: {block.title}")

        beats = _block_sync_beats(sync, block.number)
        runs = _block_visual_runs(sync, block.number)
        if not beats:
            raise AssemblyError(f"O bloco {block.number} nao possui batidas sincronizadas")
        if not runs:
            raise AssemblyError(f"O bloco {block.number} nao possui tomadas do Continuity Engine")

        voice_wav: Path | None = None
        use_voice = settings.audio_mode != "original" and block.needs_voice
        if use_voice:
            recording = approved_recording(paths, block.number)
            voice_wav = working / f"bloco_{block.number:03d}_voz_normalizada.wav"
            normalize_voice_audio(recording, voice_wav)
            target_seconds = audio_duration(voice_wav)
            if target_seconds < 0.5:
                raise AssemblyError(f"A narracao do bloco {block.number} esta curta demais")
        else:
            target_seconds = sum(run.duration for run in runs)

        rendered: list[Path] = []
        block_report_runs = []
        feature_intervals: list[tuple[float, float]] = []
        block_output_origin = min((run.output_start_seconds for run in runs), default=0.0)
        for run_index, run in enumerate(runs, start=1):
            if run.verification == "failed" or not run.filename:
                raise AssemblyError(f"Tomada {run.run_id} sem evidencia valida")
            source = paths.footage / run.filename
            if not source.exists():
                raise AssemblyError(f"Filmagem nao encontrada: {run.filename}")
            wanted = run.duration
            available = max(0.0, run.source_end_seconds - run.source_start_seconds)
            requires_punch_in = False
            source_info = probe_media(source)

            # Auto-Healer Cinematografico em 4 Estagios (Gemini 4 / v8.0)
            if available + 0.08 < wanted:
                heal_result = auto_heal_scene_duration_mismatch(
                    scene_id=f"b{block.number:03d}",
                    take_id=run.run_id,
                    clip_duration=available,
                    required_duration=wanted,
                    audio_path=voice_wav,
                    source_video_duration=source_info.duration,
                    clip_in_point=run.source_start_seconds,
                    clip_out_point=run.source_end_seconds,
                )
                strategy = heal_result.get("strategy", "")
                if strategy == "handle_expansion":
                    run.source_start_seconds = heal_result["in_point"]
                    run.source_end_seconds = heal_result["out_point"]
                    available = max(0.0, run.source_end_seconds - run.source_start_seconds)
                    logger(
                        f"[Auto-Heal 88%] Tomada {run.run_id}: handles expandidos para "
                        f"[{run.source_start_seconds:.2f}-{run.source_end_seconds:.2f}]s "
                        f"(disponivel={available:.2f}s, necessario={wanted:.2f}s)"
                    )
                elif strategy.startswith("smart_audio_pacing"):
                    effective_audio_dur = heal_result["effective_audio_duration"]
                    wanted = available
                    run.duration = available
                    if voice_wav and voice_wav.exists():
                        target_seconds = audio_duration(voice_wav)
                    logger(
                        f"[Auto-Heal 88%] Tomada {run.run_id}: cadencia da narracao ajustada ({strategy}) "
                        f"para {available:.2f}s cravados."
                    )
                elif heal_result.get("requires_dynamic_punch_in"):
                    requires_punch_in = True
                    logger(
                        f"[Auto-Heal 88%] Tomada {run.run_id}: aplicando Dynamic Punch-In Jump-Cut (118% scale) "
                        f"para cobrir duracao total de {wanted:.2f}s sem congelar frame."
                    )
                else:
                    if source_info.duration > available:
                        run.source_end_seconds = min(source_info.duration, run.source_start_seconds + wanted)
                        available = max(0.0, run.source_end_seconds - run.source_start_seconds)
                    if available + 0.08 < wanted:
                        requires_punch_in = True

            output = working / f"b{block.number:03d}_run{run_index:03d}.mp4"
            logger(
                f"RUN {run.run_id}: {run.filename} "
                f"[{run.source_start_seconds:.2f}-{run.source_start_seconds + wanted:.2f}] "
                f"=> [{run.output_start_seconds:.2f}-{run.output_end_seconds:.2f}] "
                f"beats={','.join(run.beat_ids)} verification={run.verification}"
            )
            effective_audio = run.audio_strategy
            if voice_wav and effective_audio == "feature":
                feature_intervals.append((
                    max(0.0, run.output_start_seconds - block_output_origin),
                    max(0.0, run.output_end_seconds - block_output_origin),
                ))

            if requires_punch_in:
                render_punch_in_composite_clip(
                    source=source, destination=output, start=run.source_start_seconds,
                    available_duration=available, wanted_duration=wanted,
                    width=width, height=height, fps=settings.fps, format_mode=settings.format,
                    focus_x=run.focus_x, focus_y=run.focus_y, vertical_strategy=run.vertical_strategy,
                    audio_strategy=effective_audio, punch_in_scale=1.18,
                )
            else:
                render_clip(
                    source=source, destination=output, start=run.source_start_seconds, duration=wanted,
                    width=width, height=height, fps=settings.fps, format_mode=settings.format,
                    focus_x=run.focus_x, focus_y=run.focus_y, vertical_strategy=run.vertical_strategy,
                    audio_strategy=effective_audio,
                )
            actual = probe_media(output).duration
            if abs(actual - wanted) > 0.25:
                logger(f"[Aviso] Tomada {run.run_id} renderizou {actual:.2f}s vs {wanted:.2f}s desejados.")
            rendered.append(output)
            block_report_runs.append({
                "run_id": run.run_id, "beat_ids": run.beat_ids, "event_ids": run.event_ids,
                "filename": run.filename, "output_start_seconds": round(run.output_start_seconds, 3),
                "output_end_seconds": round(run.output_end_seconds, 3),
                "source_start_seconds": round(run.source_start_seconds, 3),
                "source_end_seconds": round(run.source_start_seconds + wanted, 3),
                "verification": run.verification, "confidence": run.confidence, "reason": run.reason,
                "audio_strategy": effective_audio,
            })

        visual = working / f"bloco_{block.number:03d}_visual.mp4"
        concat_videos(rendered, visual)
        visual_duration = probe_media(visual).duration
        if voice_wav and abs(visual_duration - target_seconds) > 0.20:
            speed_ratio = target_seconds / max(0.1, visual_duration)
            if 0.80 <= speed_ratio <= 1.25:
                logger(
                    f"[Auto-Heal] Sincronismo do bloco {block.number}: ajustando cadencia da voz "
                    f"de {target_seconds:.2f}s para {visual_duration:.2f}s (ratio={speed_ratio:.3f})"
                )
                success, actual_dur = _tighten_narration_audio(
                    voice_wav, target_duration=visual_duration, speed_ratio=speed_ratio
                )
                if success:
                    target_seconds = actual_dur
            if abs(visual_duration - target_seconds) > 0.35:
                logger(
                    f"[Aviso] Sincronismo do bloco {block.number} divergiu ligeiramente: "
                    f"visual={visual_duration:.2f}s voz={target_seconds:.2f}s"
                )

        block_output = working / f"bloco_{block.number:03d}_final.mp4"
        if voice_wav:
            mix_narration(visual, voice_wav, block_output, target_seconds, feature_intervals)
        else:
            shutil.copy2(visual, block_output)
        block_outputs.append(block_output)

        report["blocks"].append({
            "number": block.number,
            "title": block.title,
            "narration_seconds": round(target_seconds, 3) if voice_wav else 0,
            "block_seconds": round(target_seconds, 3),
            "visual_runs": block_report_runs,
            "semantic_beats": [b.beat_id for b in beats],
        })

    progress(90, "Unindo blocos em qualidade interna alta")
    raw_master = working / "MASTER_ASSEMBLY_RAW.mp4"
    concat_videos(block_outputs, raw_master)

    master_path = paths.result / "MASTER_DRSAYGON_VIDEO_FACTORY.mp4"
    delivery_path = paths.result / "CONTEUDO_PRINCIPAL.mp4"
    generated: list[Path] = []

    # Never leave a stale file looking current when that output is disabled.
    if not settings.create_master:
        master_path.unlink(missing_ok=True)
    if not settings.create_delivery:
        delivery_path.unlink(missing_ok=True)

    if settings.create_master:
        master_label = "Master HQ" if settings.master_profile == "premiere_hq" else "Master Máxima"
        progress(92, f"Gerando {master_label} para o Premiere")
        export_master(raw_master, master_path, settings.fps, settings.master_profile)
        generated.append(master_path)

    if settings.create_delivery:
        label = "YouTube 1080p HQ" if settings.delivery_profile == "youtube_1080p" else "Redes sociais 1080p HQ"
        progress(96 if settings.create_master else 93, f"Gerando arquivo de publicação {label}")
        export_delivery(raw_master, delivery_path, settings.delivery_profile, settings.fps)
        generated.append(delivery_path)

    if not generated:
        raise RuntimeError("Nenhuma saída de vídeo foi selecionada")

    primary_path = delivery_path if delivery_path in generated else master_path
    final_duration = probe_media(primary_path).duration
    report["final_duration_seconds"] = round(final_duration, 3)
    report["target_duration_seconds"] = settings.target_duration_seconds
    report["audio_mode"] = settings.audio_mode
    report["create_master"] = settings.create_master
    report["master_profile"] = settings.master_profile
    report["create_delivery"] = settings.create_delivery
    report["delivery_profile"] = settings.delivery_profile
    master_profile_text = (
        "H.264 High ~50 Mb/s (<=30 fps) / ~70 Mb/s (>30 fps)"
        if settings.master_profile == "premiere_hq"
        else "H.264 High ~80 Mb/s (<=30 fps) / ~100 Mb/s (>30 fps)"
    )
    report["outputs"] = {
        "master": master_path.name if settings.create_master and master_path.exists() else None,
        "delivery": delivery_path.name if settings.create_delivery and delivery_path.exists() else None,
        "primary": primary_path.name,
        "metadata_brand": "DrSaygon Video Factory",
        "master_video_profile": master_profile_text,
    }

    if settings.format == "short_9_16" and not 30 <= final_duration <= 120:
        report["warnings"].append(
            "A duracao final ficou fora da faixa de Shorts configurada (30 a 120 segundos)."
        )
    write_json(paths.result / "RELATORIO_DE_EDICAO.json", report)
    progress(100, "Saídas concluídas com qualidade e metadados DrSaygon Video Factory")
    return primary_path
