from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from app.services.publication import normalize_publication_package, normalize_tags
from app.services.description_composer import compose_description
from app.services.web_research import collect_official_web_research

from app.config import (
    GEMINI_ANALYSIS_FPS,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_MODEL_FALLBACKS,
    GEMINI_MODEL_PRIMARY,
    GEMINI_FALLBACK_MODELS,
    GEMINI_PRECISION_MODEL,
    GEMINI_VERIFY_FPS,
    GEMINI_ROUGH_QA_FPS,
)
from app.models import (
    AudioAlignmentPlan,
    EditPlan,
    PublicationPackage,
    PublicationSource,
    PublicationContext,
    PublicationFactSheet,
    PublicationDescriptionBlueprint,
    SyncMatchPlan,
    VideoAnalysis,
    VisualVerification,
    DirectedClipFinding,
    RoughCutReview,
    ChronologyInference,
)

T = TypeVar("T", bound=BaseModel)

# Runtime circuit breaker for models that this API key/project cannot use.
# A NOT_FOUND/404 is treated as unavailable for the rest of the process so a
# 12-window evidence search does not waste one failed request per window.
_UNAVAILABLE_MODELS: set[str] = set()


def _is_permanent_model_error(error: Exception) -> bool:
    text = f"{type(error).__name__}: {error}".lower()
    return any(marker in text for marker in (
        "404", "not found", "not_found", "model is not found",
        "model not found", "is not supported for generatecontent",
    ))


class GeminiConfigurationError(RuntimeError):
    pass


class GeminiResponseError(RuntimeError):
    pass


def get_client() -> genai.Client:
    if not GEMINI_API_KEY:
        raise GeminiConfigurationError(
            "A chave GEMINI_API_KEY nao foi configurada. Execute CONFIGURAR_CHAVE.bat."
        )
    return genai.Client(api_key=GEMINI_API_KEY)


def model_candidates(prefer_precision: bool = False) -> list[str]:
    """Return models in an explicit, stable order.

    v4.0.0 automatically moved every model containing "2.5" to the front for
    verification. That assumption is unsafe: model availability is scoped by
    key/project/endpoint and can change. v4.0.1 only uses an explicit
    GEMINI_PRECISION_MODEL when configured; otherwise the known-working primary
    model stays first and fallbacks are true fallbacks.
    """
    raw: list[str] = []
    if prefer_precision and GEMINI_PRECISION_MODEL:
        raw.append(GEMINI_PRECISION_MODEL)
    raw.extend([GEMINI_MODEL, *GEMINI_MODEL_FALLBACKS])
    candidates: list[str] = []
    for model in raw:
        if model and model not in candidates and model not in _UNAVAILABLE_MODELS:
            candidates.append(model)
    return candidates


def parse_structured_response(text: str, schema: type[T]) -> T:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        cleaned = cleaned.rsplit("```", 1)[0]
    try:
        return schema.model_validate_json(cleaned)
    except ValidationError as error:
        try:
            return schema.model_validate(json.loads(cleaned))
        except Exception as nested:
            raise GeminiResponseError(f"Resposta JSON invalida: {error}") from nested


class ApexGeminiService:
    def __init__(self, api_key: str | None = None):
        key = api_key or GEMINI_API_KEY
        self.client = genai.Client(api_key=key) if key else genai.Client()
        self.primary_model = GEMINI_MODEL_PRIMARY

    def generate_structured_json(
        self,
        contents: list,
        system_instruction: str,
        response_schema: type | dict | None = None,
        temperature: float = 0.2,
    ) -> str:
        """Executa chamada ao Gemini 3.8+ com JSON Schema garantido e fallback resiliente."""
        models_to_try = [self.primary_model] + [
            m for m in GEMINI_FALLBACK_MODELS if m != self.primary_model
        ]
        last_error = None

        for model_name in models_to_try:
            try:
                config_kwargs: dict[str, Any] = {
                    "system_instruction": system_instruction,
                    "temperature": temperature,
                    "response_mime_type": "application/json",
                }
                if response_schema is not None:
                    config_kwargs["response_schema"] = response_schema

                response = self.client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
                if response and response.text:
                    return response.text
            except Exception as exc:
                last_error = exc
                continue

        raise RuntimeError(f"Falha ao consultar Gemini 3.8+: {last_error}")


def _structured_config(schema: type[T], max_output_tokens: int) -> types.GenerateContentConfig:
    """Build a structured-output config while remaining compatible with older SDKs."""
    kwargs = dict(
        max_output_tokens=max_output_tokens,
        response_mime_type="application/json",
        response_schema=schema,
    )
    try:
        kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
        return types.GenerateContentConfig(**kwargs)
    except (AttributeError, TypeError):
        kwargs.pop("automatic_function_calling", None)
        return types.GenerateContentConfig(**kwargs)


def _generate_with_fallback(
    contents: list[Any],
    schema: type[T],
    *,
    max_output_tokens: int,
    client: genai.Client | None = None,
    prefer_precision: bool = False,
) -> tuple[T, str]:
    owns_client = client is None
    active_client = client or get_client()
    errors: list[str] = []

    try:
        for model in model_candidates(prefer_precision=prefer_precision):
            try:
                response = active_client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=_structured_config(schema, max_output_tokens),
                )
                if not response.text:
                    raise GeminiResponseError("O Gemini retornou uma resposta vazia")
                return parse_structured_response(response.text, schema), model
            except Exception as error:
                errors.append(f"{model}: {type(error).__name__}: {error}")
                if _is_permanent_model_error(error):
                    _UNAVAILABLE_MODELS.add(model)
    finally:
        if owns_client:
            active_client.close()

    raise GeminiResponseError("Nenhum modelo respondeu com sucesso.\n" + "\n".join(errors))


def wait_until_active(
    client: genai.Client,
    uploaded: Any,
    logger: Callable[[str], None],
) -> Any:
    deadline = time.time() + 30 * 60
    current = uploaded
    while time.time() < deadline:
        state_name = getattr(getattr(current, "state", None), "name", None)
        if state_name == "ACTIVE" or state_name is None:
            return current
        if state_name == "FAILED":
            raise GeminiResponseError(
                f"O Google nao conseguiu processar o arquivo {current.name}"
            )
        logger("Aguardando o Gemini processar o arquivo...")
        time.sleep(4)
        current = client.files.get(name=current.name)
    raise TimeoutError("O processamento do arquivo no Gemini excedeu 30 minutos")


def _video_part(uploaded: Any, fps: float) -> Any:
    """Build a video part with high temporal sampling when supported by the SDK.

    The default File API visual sampling is 1 FPS, which can miss plug/unplug,
    button presses and flicker/banding transitions. Current Gemini docs support
    overriding FPS with VideoMetadata. A compatibility fallback keeps older SDKs
    functional, albeit with lower precision.
    """
    try:
        return types.Part(
            file_data=types.FileData(
                file_uri=uploaded.uri,
                mime_type=uploaded.mime_type,
            ),
            video_metadata=types.VideoMetadata(fps=float(fps)),
        )
    except Exception:
        return types.Part.from_uri(file_uri=uploaded.uri, mime_type=uploaded.mime_type)


INLINE_VIDEO_MAX_BYTES = 18 * 1024 * 1024


def _inline_video_part(path: Path, fps: float) -> Any:
    """Use inline video for small verification clips.

    This avoids the File API storage sampling path for the very clips where
    frame-level timing matters most. The official Gemini video API supports
    custom FPS through VideoMetadata for inline video.
    """
    return types.Part(
        inline_data=types.Blob(data=path.read_bytes(), mime_type="video/mp4"),
        video_metadata=types.VideoMetadata(fps=float(fps)),
    )


def _video_path_generate(
    path: Path,
    fps: float,
    prompt: str,
    schema: type[T],
    *,
    max_output_tokens: int,
    logger: Callable[[str], None],
    prefer_precision: bool = True,
) -> tuple[T, str]:
    """Generate against a local video, preferring inline bytes for microclips."""
    with get_client() as client:
        if path.stat().st_size <= INLINE_VIDEO_MAX_BYTES:
            return _generate_with_fallback(
                [_inline_video_part(path, fps), prompt], schema,
                max_output_tokens=max_output_tokens, client=client,
                prefer_precision=prefer_precision,
            )

        uploaded = client.files.upload(file=str(path))
        try:
            uploaded = wait_until_active(client, uploaded, logger)
            return _generate_with_fallback(
                [_video_part(uploaded, fps), prompt], schema,
                max_output_tokens=max_output_tokens, client=client,
                prefer_precision=prefer_precision,
            )
        finally:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


def analyze_video(
    proxy_path: Path,
    original_filename: str,
    original_duration: float,
    prompt_text: str,
    logger: Callable[[str], None],
    context_text: str = "",
) -> tuple[VideoAnalysis, str]:
    from app.services.ffmpeg import extract_physical_media_map, apply_snap_to_cut
    from app.services.voice_profile import calculate_phonetic_budget
    from app.models import VisualAnchor, PhoneticBudget

    physical_map = extract_physical_media_map(proxy_path)
    safe_cuts = physical_map.get("safe_cut_timestamps", [])

    with get_client() as client:
        logger(f"Enviando proxy de evidencias para o Gemini: {proxy_path.name}")
        uploaded = client.files.upload(file=str(proxy_path))
        try:
            uploaded = wait_until_active(client, uploaded, logger)
            physical_ctx = (
                f"\n\nSINAIS FÍSICOS REAIS DE CENA E ÁUDIO (FFMPEG):\n"
                f"- Pontos de corte seguro ('Cortes Ouro'): {json.dumps(safe_cuts)}\n"
                f"- Janelas de silêncio: {json.dumps(physical_map.get('silence_windows', []))}\n"
                f"Priorize os timestamps de corte seguro para início/fim das cenas."
            )
            prompt = prompt_text.format(
                filename=original_filename,
                duration_seconds=f"{original_duration:.3f}",
                project_context=(context_text or "[Sem contexto adicional]") + physical_ctx,
            )
            contents = [
                _video_part(uploaded, GEMINI_ANALYSIS_FPS),
                prompt,
            ]
            result, model = _generate_with_fallback(
                contents,
                VideoAnalysis,
                max_output_tokens=32000,
                client=client,
                prefer_precision=True,
            )
            result.filename = original_filename
            result.duration_seconds = original_duration

            # Post-processing: Magnetic Snap-to-Cut + VisualAnchor + PhoneticBudget
            for seg in result.segments:
                if safe_cuts:
                    new_start = apply_snap_to_cut(seg.start_seconds, safe_cuts, threshold=0.40)
                    new_end = apply_snap_to_cut(seg.end_seconds, safe_cuts, threshold=0.40)
                    if new_end > new_start + 0.2:
                        seg.start_seconds = new_start
                        seg.end_seconds = new_end

                duration = max(0.3, seg.end_seconds - seg.start_seconds)
                if not seg.visual_anchor or not (seg.start_seconds <= seg.visual_anchor.timestamp_sec <= seg.end_seconds):
                    anchor_time = seg.evidence_timestamps[0] if (seg.evidence_timestamps and seg.start_seconds <= seg.evidence_timestamps[0] <= seg.end_seconds) else round(seg.start_seconds + duration * 0.45, 3)
                    seg.visual_anchor = VisualAnchor(
                        timestamp_sec=anchor_time,
                        trigger_phrase=seg.description[:80] if seg.description else "",
                        action_label=seg.event_kind or "evidencia_cena",
                        confidence=seg.confidence,
                    )
                budget_data = calculate_phonetic_budget(duration)
                seg.phonetic_budget = PhoneticBudget(**budget_data)

            return result, model
        finally:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


def verify_visual_event(
    verification_clip: Path,
    beat_text: str,
    required_observation: str,
    prompt_text: str,
    logger: Callable[[str], None] | None = None,
) -> tuple[VisualVerification, str]:
    """Adversarial second pass over the actual candidate micro-clip."""
    log = logger or (lambda _: None)
    prompt = prompt_text.format(
        beat_text=beat_text,
        required_observation=required_observation or beat_text,
    )
    return _video_path_generate(
        verification_clip, GEMINI_VERIFY_FPS, prompt, VisualVerification,
        max_output_tokens=5000, logger=log, prefer_precision=True,
    )



def find_directed_evidence(
    slowed_clip: Path,
    target: str,
    source_context: str,
    prompt_text: str,
    logger: Callable[[str], None] | None = None,
) -> tuple[DirectedClipFinding, str]:
    """Search one deliberately slowed micro-clip for one explicit visual target."""
    log = logger or (lambda _: None)
    prompt = prompt_text.format(target=target, source_context=source_context)
    return _video_path_generate(
        slowed_clip, max(GEMINI_VERIFY_FPS, 12.0), prompt, DirectedClipFinding,
        max_output_tokens=6000, logger=log, prefer_precision=True,
    )



def review_rough_cut(
    draft_path: Path,
    beats: list[dict[str, Any]],
    locked_facts: list[str],
    chronology_context: dict[str, Any],
    prompt_text: str,
    logger: Callable[[str], None] | None = None,
) -> tuple[RoughCutReview, str]:
    """Independent critic over one low-resolution edit/chunk with narration."""
    log = logger or (lambda _: None)
    prompt = prompt_text.format(
        beats_json=json.dumps(beats, ensure_ascii=False, indent=2),
        locked_facts_json=json.dumps(locked_facts, ensure_ascii=False, indent=2),
        chronology_json=json.dumps(chronology_context, ensure_ascii=False, indent=2),
    )
    return _video_path_generate(
        draft_path, max(1.0, GEMINI_ROUGH_QA_FPS), prompt, RoughCutReview,
        max_output_tokens=12000, logger=log, prefer_precision=True,
    )



def align_narration_audio(
    audio_path: Path,
    beats: list[dict[str, Any]],
    duration_seconds: float,
    prompt_text: str,
    logger: Callable[[str], None] | None = None,
) -> tuple[AudioAlignmentPlan, str]:
    """Align known script beats against the actual approved narration audio."""
    log = logger or (lambda _: None)
    prompt = prompt_text.format(
        beats_json=json.dumps(beats, ensure_ascii=False, indent=2),
        duration_seconds=f"{duration_seconds:.3f}",
    )
    with get_client() as client:
        log(f"Enviando narracao aprovada para alinhamento temporal: {audio_path.name}")
        uploaded = client.files.upload(file=str(audio_path))
        try:
            uploaded = wait_until_active(client, uploaded, log)
            part = types.Part.from_uri(file_uri=uploaded.uri, mime_type=uploaded.mime_type)
            return _generate_with_fallback(
                [part, prompt],
                AudioAlignmentPlan,
                max_output_tokens=12000,
                client=client,
                prefer_precision=True,
            )
        finally:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


def create_sync_matches(
    beats: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    prompt_text: str,
    chronology_context: dict[str, Any] | None = None,
    locked_facts: list[str] | None = None,
) -> tuple[SyncMatchPlan, str]:
    prompt = prompt_text.format(
        beats_json=json.dumps(beats, ensure_ascii=False, indent=2),
        evidence_json=json.dumps(evidence, ensure_ascii=False, indent=2),
        chronology_json=json.dumps(chronology_context or {}, ensure_ascii=False, indent=2),
        locked_facts_json=json.dumps(locked_facts or [], ensure_ascii=False, indent=2),
    )
    # Deliberately keep the semantic matcher on the normal model order while
    # video analysis/verification prefer the 2.5-series model. This gives the
    # critical gate a partially independent decision path instead of asking one
    # identical model/prompt chain to approve its own choice.
    return _generate_with_fallback(
        [prompt],
        SyncMatchPlan,
        max_output_tokens=24000,
        prefer_precision=False,
    )


def _upload_information_pdfs(
    client: genai.Client,
    files: list[Path],
    logger: Callable[[str], None] | None = None,
) -> tuple[list[Any], list[Any]]:
    parts: list[Any] = []
    uploaded_files: list[Any] = []
    for path in files:
        if path.suffix.lower() != ".pdf" or not path.exists():
            continue
        if path.stat().st_size > 30 * 1024 * 1024:
            if logger:
                logger(f"PDF ignorado por ser maior que 30 MB: {path.name}")
            continue
        if logger:
            logger(f"Enviando PDF de referencia ao Gemini: {path.name}")
        uploaded = client.files.upload(file=str(path))
        uploaded = wait_until_active(client, uploaded, logger or (lambda _: None))
        uploaded_files.append(uploaded)
        parts.append(types.Part.from_uri(file_uri=uploaded.uri, mime_type=uploaded.mime_type))
    return parts, uploaded_files


def create_edit_plan(
    catalog: dict[str, Any],
    project_data: dict[str, Any],
    information_text: str,
    information_files: list[Path],
    prompt_text: str,
    logger: Callable[[str], None] | None = None,
    knowledge_data: dict[str, Any] | None = None,
) -> tuple[EditPlan, str]:
    prompt = prompt_text.format(
        catalog_json=json.dumps(catalog, ensure_ascii=False, indent=2),
        project_json=json.dumps(project_data, ensure_ascii=False, indent=2),
        information_text=information_text or "[Nenhuma observacao textual adicional]",
        knowledge_json=json.dumps(knowledge_data or {}, ensure_ascii=False, indent=2),
    )

    with get_client() as client:
        uploaded_files: list[Any] = []
        try:
            pdf_parts, uploaded_files = _upload_information_pdfs(
                client,
                information_files,
                logger,
            )
            contents: list[Any] = [*pdf_parts, prompt]
            return _generate_with_fallback(
                contents,
                EditPlan,
                max_output_tokens=36000,
                client=client,
                prefer_precision=True,
            )
        finally:
            for uploaded in uploaded_files:
                try:
                    client.files.delete(name=uploaded.name)
                except Exception:
                    pass


def _grounding_sources(response: Any) -> list[PublicationSource]:
    """Extract real web sources returned by Google Search grounding.

    The SDK shape has changed across releases, so this deliberately uses
    defensive getattr access. We never synthesize URLs.
    """
    found: list[PublicationSource] = []
    seen: set[str] = set()
    try:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return found
        meta = getattr(candidates[0], "grounding_metadata", None)
        chunks = getattr(meta, "grounding_chunks", None) or []
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            if not web:
                continue
            url = str(getattr(web, "uri", "") or "").strip()
            title = str(getattr(web, "title", "") or "").strip()
            if not url.lower().startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            found.append(PublicationSource(title=title, url=url))
    except Exception:
        return found
    return found[:8]


def infer_publication_context(
    project_data: dict[str, Any],
    plan: EditPlan,
    information_text: str,
    logger: Callable[[str], None] | None = None,
) -> PublicationContext:
    """Infer a generic publication strategy before any web research.

    This prevents the application from treating every project as a product
    review. The same workflow can serve reviews, kids/family videos, vlogs,
    tutorials, events, gaming, recipes, education and arbitrary subjects.
    """
    log = logger or (lambda _msg: None)
    plan_summary = "\n".join(
        f"- {block.title}: {block.narration}"
        for block in plan.blocks
        if getattr(block, "narration", "")
    )[:12000]
    prompt = f"""
Classifique o contexto editorial deste projeto para um gerador GENÉRICO de metadados de YouTube.

PROJETO
{json.dumps(project_data, ensure_ascii=False, indent=2)}

ROTEIRO
{plan_summary}

OBSERVAÇÕES DO AUTOR
{information_text or '[nenhuma]'}

REGRAS
1. Não presuma que o campo interno 'product' contém um produto. Ele pode conter tema, pessoa, evento, local, jogo ou ficar vazio.
2. content_type deve representar o vídeo real.
3. is_product_specific=true SOMENTE quando existe um produto/equipamento/modelo específico como assunto central.
4. technical_specs_relevant=true SOMENTE para produto/equipamento em que ficha técnica ajuda a audiência.
5. research_mode:
   - product_official: produto/modelo específico; buscar fabricante/manual/ficha técnica.
   - subject_official: evento, local, organização, software, jogo ou outro tema em que fonte oficial agrega fatos úteis.
   - none: vídeo pessoal/infantil/familiar/entretenimento ou tema em que pesquisa externa não agrega valor ou criaria risco de inventar contexto.
6. Para vídeo infantil/familiar, seja conservador: não pesquise dados pessoais desnecessários; use apenas o que o autor/roteiro já fornece, salvo local/evento público claramente relevante.
7. research_query deve ser curta e específica. Para produto, inclua marca/modelo exatos. Para tema sem pesquisa, deixe vazio.
8. search_intents: 6 a 12 consultas/frases que uma pessoa poderia pesquisar para encontrar ESTE vídeo, sem keyword stuffing.
9. description_focus: seções/ângulos que realmente ajudam a audiência deste tipo de vídeo.
"""
    try:
        context, model = _generate_with_fallback([prompt], PublicationContext, max_output_tokens=5000)
        if not context.primary_subject:
            context.primary_subject = str(project_data.get("product") or project_data.get("name") or plan.project_title or "").strip()
        if context.is_product_specific and not context.product_name:
            context.product_name = context.primary_subject
        if not context.factual_research_relevant:
            context.research_mode = "none"
        log(f"Contexto de publicação: {context.content_type} | pesquisa={context.research_mode} | modelo={model}.")
        return context
    except Exception as error:
        # Safe generic fallback: no external/product assumptions.
        log(f"Classificação editorial falhou; usando contexto genérico seguro: {error}")
        subject = str(project_data.get("product") or project_data.get("name") or plan.project_title or "").strip()
        return PublicationContext(
            content_type="other",
            primary_subject=subject,
            research_mode="none",
            factual_research_relevant=False,
            technical_specs_relevant=False,
            search_intents=[subject] if subject else [],
            description_focus=["o que acontece no vídeo", "informações úteis para a audiência"],
        )


def extract_official_fact_sheet(
    context: PublicationContext,
    research_text: str,
    author_information: str = "",
    logger: Callable[[str], None] | None = None,
) -> PublicationFactSheet:
    """Extract exact-model facts before asking the writer to compose prose.

    A separate structured extraction prevents generic family marketing text from
    masquerading as model-specific technical specifications.
    """
    log = logger or (lambda _msg: None)
    if not context.is_product_specific or not context.technical_specs_relevant or not research_text.strip():
        return PublicationFactSheet(subject=context.product_name or context.primary_subject)
    prompt = f"""
Extraia uma ficha factual ESTRUTURADA do produto exato abaixo usando SOMENTE a evidência fornecida.

PRODUTO EXATO
{context.product_name or context.primary_subject}

EVIDÊNCIA DE FONTES OFICIAIS/PRIMÁRIAS
{research_text}

INFORMAÇÕES FACTUAIS FORNECIDAS PELO AUTOR (podem complementar a ficha quando forem afirmações explícitas, nunca inferências)
{author_information or '[nenhuma]'}

REGRAS ABSOLUTAS
1. exact_subject_match=true somente se a evidência realmente se refere ao mesmo modelo/variante. Não misture família, tensão, revisão ou capacidade diferente.
2. Extraia o MÁXIMO de dados concretos disponíveis para o modelo exato. specifications deve conter pares label/value concretos: dimensões, peso, potência/capacidade, entrada/saída, frequência, bateria, autonomia, tomadas/conexões, forma de onda, transferência, proteção, indicadores, cor, código/EAN/GTIN, garantia e outros campos úteis. SOMENTE se aparecerem na evidência oficial/primária ou forem explicitamente informados pelo autor.
3. features são recursos/funções confirmados do modelo exato. Preserve detalhes úteis, inclusive proteções, restart, DC start, carregamento, modos, compatibilidades e demais recursos quando confirmados.
4. package_contents contém somente itens de embalagem confirmados.
5. Não use frases genéricas como "protege equipamentos" no lugar de especificações ausentes.
6. Não estime nem converta valores se a fonte não os fornecer claramente.
7. Se houver vários valores da mesma grandeza, mantenha o contexto/condição no value (por exemplo autonomia por tipo de carga).
8. package_contents deve ser completo quando a fonte listar o conteúdo da embalagem.
"""
    try:
        sheet, model = _generate_with_fallback([prompt], PublicationFactSheet, max_output_tokens=8000)
        log(f"Ficha factual oficial: exact={sheet.exact_subject_match}, specs={len(sheet.specifications)}, itens={len(sheet.package_contents)} | {model}.")
        return sheet
    except Exception as error:
        log(f"Extração da ficha factual falhou; nenhuma especificação será inventada: {error}")
        return PublicationFactSheet(subject=context.product_name or context.primary_subject)


def research_publication_sources(
    project_data: dict[str, Any],
    plan: EditPlan,
    information_text: str,
    context: PublicationContext,
    logger: Callable[[str], None] | None = None,
) -> tuple[str, list[PublicationSource], str]:
    """Research current, preferably first-party facts before writing metadata.

    This is intentionally a separate call from structured JSON generation.
    The default path searches/fetches likely official pages without a paid API;
    optional Google Search grounding is only a fallback. The structured model
    is then allowed to write specifications only from the retrieved evidence.
    """
    log = logger or (lambda _msg: None)
    subject = str(context.product_name or context.primary_subject or project_data.get("product") or project_data.get("name") or "").strip()
    if context.research_mode == "none" or not context.factual_research_relevant:
        return "[Pesquisa web dispensada pelo contexto editorial: o vídeo não exige fatos externos para ser publicado com qualidade.]", [], "skipped"
    if not subject:
        return "[Pesquisa web não executada: assunto principal não identificado.]", [], "skipped"

    plan_summary = "\n".join(
        f"- {block.title}: {block.narration}"
        for block in plan.blocks
        if getattr(block, "narration", "")
    )[:12000]
    # Free-first official research: normal HTTP search + direct manufacturer
    # page fetch. This keeps the default workflow from unexpectedly requiring
    # paid Google Search grounding.
    free_text, free_sources = collect_official_web_research(subject, logger=log, mode=context.research_mode, research_query=context.research_query)
    if free_text and free_sources:
        sources = [PublicationSource(title=item.get("title", ""), url=item.get("url", "")) for item in free_sources]
        log(f"Pesquisa oficial gratuita concluida: {len(sources)} fonte(s) direta(s).")
        return free_text, sources, "official_web"

    research_prompt = f"""
Você é um pesquisador factual para metadados de vídeo. Pesquise na Web informações ATUAIS sobre:
{subject}

CONTEXTO EDITORIAL
{context.model_dump_json(indent=2)}

CONTEXTO DO VÍDEO
{plan_summary}

OBSERVAÇÕES DO AUTOR
{information_text or '[nenhuma]'}

REGRAS DE PESQUISA
1. Respeite research_mode={context.research_mode}.
2. Em product_official, priorize fabricante, página do modelo exato, manual, ficha técnica, FAQ e suporte. Não misture variantes/modelos.
3. Em subject_official, priorize fontes primárias/autoridades oficiais úteis ao assunto. Não force especificações de produto.
4. Ignore marketplaces, agregadores e blogs SEO quando houver fonte primária.
5. Não transforme inferência visual do vídeo em fato externo.
6. Não invente preço, datas, medidas, biografia, especificações ou contexto.
7. Para conteúdo infantil/familiar, não pesquise nem acrescente dados pessoais desnecessários.
8. Responda em português brasileiro com relatório factual conciso.
9. Não escreva título, descrição ou tags ainda.
"""

    import os
    if os.getenv("DRSAYGON_ALLOW_GOOGLE_SEARCH_GROUNDING", "0").strip().lower() not in {"1", "true", "yes", "sim"}:
        log("Pesquisa oficial gratuita nao encontrou fonte suficientemente confiavel. Google Search grounding esta desativado por padrao para evitar custo inesperado.")
        return "[Pesquisa web oficial nao encontrou fonte direta confiavel nesta execucao. Nao acrescente especificacoes externas.]", [], "unavailable"

    errors: list[str] = []
    with get_client() as client:
        for model in model_candidates():
            try:
                tool = types.Tool(google_search=types.GoogleSearch())
                kwargs: dict[str, Any] = {
                    "tools": [tool],
                    "max_output_tokens": 8000,
                    "temperature": 0.15,
                }
                try:
                    kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
                except Exception:
                    pass
                response = client.models.generate_content(
                    model=model,
                    contents=[research_prompt],
                    config=types.GenerateContentConfig(**kwargs),
                )
                text = str(getattr(response, "text", "") or "").strip()
                if not text:
                    raise GeminiResponseError("Pesquisa Google retornou resposta vazia")
                sources = _grounding_sources(response)
                log(f"Pesquisa web para publicacao concluida com {model}: {len(sources)} fonte(s) grounded.")
                return text, sources, "grounded"
            except Exception as error:
                errors.append(f"{model}: {type(error).__name__}: {error}")
                if _is_permanent_model_error(error):
                    _UNAVAILABLE_MODELS.add(model)

    log("Pesquisa web oficial indisponivel; pacote sera gerado sem inventar especificacoes externas.")
    return "[Pesquisa web oficial indisponivel nesta execucao. Nao acrescente especificacoes que nao estejam no roteiro/observacoes.]", [], "unavailable"


def create_publication_package(
    plan: EditPlan,
    project_data: dict[str, Any],
    information_text: str,
    prompt_text: str,
    logger: Callable[[str], None] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> tuple[PublicationPackage, str]:
    log = logger or (lambda _msg: None)
    report = progress or (lambda _value, _message: None)

    report(8, "Entendendo o tipo de conteúdo e a intenção de busca")
    context = infer_publication_context(project_data, plan, information_text, logger=log)

    if context.research_mode == "none":
        report(25, "Conteúdo não exige pesquisa externa; preparando estratégia editorial")
    else:
        report(18, "Pesquisando fontes oficiais/primárias pertinentes")
    research_text, sources, research_status = research_publication_sources(
        project_data, plan, information_text, context=context, logger=log,
    )

    report(42, "Estruturando fatos confirmados")
    fact_sheet = extract_official_fact_sheet(context, research_text, information_text, logger=log)

    report(52, "Criando estratégia SEO adequada ao tipo de vídeo")
    source_json = json.dumps([s.model_dump(mode="json") for s in sources], ensure_ascii=False, indent=2)
    prompt = prompt_text.format(
        publication_context_json=context.model_dump_json(indent=2),
        project_json=json.dumps(project_data, ensure_ascii=False, indent=2),
        plan_json=json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2),
        information_text=information_text or "[Nenhuma observação adicional]",
        web_research=research_text,
        official_fact_sheet_json=fact_sheet.model_dump_json(indent=2),
        research_sources_json=source_json,
    )
    package, model = _generate_with_fallback([prompt], PublicationPackage, max_output_tokens=18000)

    report(82, "Validando título sem redundância, descrição e 25 tags")
    package = normalize_publication_package(package, project_data, plan, sources, research_status, context=context)

    description_composed = False
    report(88, "Compondo descrição completa e adaptativa")
    blueprint_prompt = f"""
Você é o editor de descrição do DrSaygon Video Factory. Gere uma ESTRUTURA de descrição completa para o vídeo abaixo.

CONTEXTO EDITORIAL
{context.model_dump_json(indent=2)}

PROJETO
{json.dumps(project_data, ensure_ascii=False, indent=2)}

ROTEIRO FINAL
{json.dumps(plan.model_dump(mode='json'), ensure_ascii=False, indent=2)}

OBSERVAÇÕES DO AUTOR
{information_text or '[nenhuma]'}

FICHA FACTUAL CONFIRMADA
{fact_sheet.model_dump_json(indent=2)}

REGRAS
1. opening_paragraphs: 2 a 4 parágrafos naturais explicando com detalhes o que acontece no vídeo, por que interessa e os conceitos centrais. SEO natural, sem keyword stuffing.
2. sections: crie somente seções úteis ao TIPO REAL do conteúdo. Para produto, NÃO escreva ficha técnica nem conteúdo da embalagem aqui: o aplicativo inserirá esses dados de forma determinística a partir da ficha factual. Você pode criar seções como 'O que foi demonstrado', 'Observações importantes', 'Conteúdo futuro'.
3. Para tutorial, vlog, viagem, vídeo infantil/familiar, gameplay, receita, educação etc., adapte completamente as seções. Não presuma produto.
4. closing_paragraphs: 1 a 2 parágrafos de encerramento/contexto futuro quando fizer sentido.
5. Não invente especificações, medidas, datas, pessoas, locais, ingredientes, passos ou fatos que não estejam no roteiro/observações/ficha factual.
6. Não mencione que você é IA e não coloque URLs.
7. O texto final deve ser informativo o bastante para alguém consultar a descrição e encontrar detalhes que não cabem na fala do vídeo.
"""
    try:
        blueprint, _blueprint_model = _generate_with_fallback([blueprint_prompt], PublicationDescriptionBlueprint, max_output_tokens=10000)
        composed = compose_description(blueprint, context, fact_sheet, max_chars=5000)
        if len(composed) >= 250:
            package.description = composed
            description_composed = True
            log(f"Descrição v5.5 composta por blueprint + ficha factual: {len(composed)} caracteres.")
    except Exception as error:
        log(f"Composer adaptativo de descrição falhou; mantendo descrição anterior: {error}")

    # Product-specific quality contract. We repair only when exact facts exist;
    # otherwise a generic family paragraph is explicitly worse than omission.
    if (not description_composed) and context.technical_specs_relevant and fact_sheet.exact_subject_match and fact_sheet.specifications:
        fact_values = [f.value.strip() for f in fact_sheet.specifications if f.value.strip()]
        visible_values = sum(1 for value in fact_values if value.casefold() in package.description.casefold())
        has_heading = bool(re.search(r"caracter[íi]sticas?.{0,40}especifica[cç][oõ]es|especifica[cç][oõ]es t[eé]cnicas", package.description, flags=re.IGNORECASE | re.DOTALL))
        min_expected = min(4, len(fact_values))
        if not has_heading or visible_values < min_expected:
            log(f"Descrição de produto não expôs a ficha factual ({visible_values}/{min_expected}); reparando sem inventar.")

            class _DescriptionRepair(BaseModel):
                description: str = Field(min_length=100, max_length=5000)

            repair_prompt = f"""
Reescreva SOMENTE a descrição abaixo, em português brasileiro, pronta para YouTube e com no máximo 5.000 caracteres.

CONTEXTO EDITORIAL
{context.model_dump_json(indent=2)}

DESCRIÇÃO ATUAL
{package.description}

FICHA FACTUAL DO MODELO EXATO
{fact_sheet.model_dump_json(indent=2)}

REGRAS OBRIGATÓRIAS
1. Preserve o resumo do vídeo e o que o espectador vai aprender/ver.
2. Inclua "Características e especificações técnicas confirmadas" em bullets.
3. Use todos os pares label/value relevantes presentes na ficha; não acrescente nenhum dado externo à ficha.
4. Se package_contents tiver itens, inclua "Conteúdo da embalagem" em bullets.
5. Não escreva texto genérico sobre a família/linha no lugar de dados do modelo exato.
6. Diferencie observação do vídeo de especificação confirmada.
7. SEO natural e legibilidade primeiro.
"""
            try:
                repaired, _ = _generate_with_fallback([repair_prompt], _DescriptionRepair, max_output_tokens=12000)
                package.description = repaired.description.strip()[:5000]
            except Exception as error:
                log(f"Reparo factual da descrição falhou; mantendo descrição sem inventar: {error}")
    elif context.technical_specs_relevant and not fact_sheet.exact_subject_match:
        # Never leave the misleading generic family section that caused v5.4's
        # poor XNB 720 description.
        package.description = re.sub(
            r"\n*Características e especificações técnicas[^\n]*\n.*?(?=\n\S|\Z)",
            "",
            package.description,
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()

    if len(package.tags) < 25:
        log(f"Pacote retornou apenas {len(package.tags)} tags únicas; solicitando complemento genérico contextual.")

        class _TagSupplement(BaseModel):
            tags: list[str] = []

        supplement_prompt = f"""
Gere SOMENTE tags adicionais para completar 25 tags fortes de busca para este vídeo.
Contexto editorial: {context.model_dump_json(indent=2)}
Título: {package.recommended_title}
Tags existentes: {json.dumps(package.tags, ensure_ascii=False)}
Use consultas reais coerentes com o tipo de conteúdo. Não assuma produto, review ou unboxing se o contexto não tiver isso. Não use hashtags. Não repita termos existentes.
"""
        try:
            extra, _ = _generate_with_fallback([supplement_prompt], _TagSupplement, max_output_tokens=5000)
            package.tags = normalize_tags([*package.tags, *extra.tags], project_data, plan, context)
        except Exception as error:
            log(f"Complemento de tags falhou: {error}")

    report(96, "Finalizando pacote de publicação")
    return package, model


def infer_footage_chronology(
    videos: list[dict[str, Any]],
    project_context: dict[str, Any],
) -> tuple[ChronologyInference, str]:
    """Infer likely real-world source order once, from already-created evidence.

    This is a soft editorial prior, never a license to invent scene content.
    """
    filenames = [str(v.get("filename", "")) for v in videos if v.get("filename")]
    compact = [
        {
            "filename": v.get("filename", ""),
            "summary": v.get("summary", ""),
            "segments": [
                {
                    "start": seg.get("start_seconds"), "end": seg.get("end_seconds"),
                    "description": seg.get("description", ""), "actions": seg.get("actions", []),
                    "states": seg.get("states", []), "phenomena": seg.get("visual_phenomena", []),
                }
                for seg in (v.get("segments") or [])[:16]
            ],
        }
        for v in videos
    ]
    prompt = f"""
Você é um montador principal. Determine a ORDEM CRONOLÓGICA MAIS PROVÁVEL dos arquivos brutos abaixo.
Use somente os fatos visíveis descritos no catálogo e os nomes dos arquivos. Não invente eventos.
Arquivos com sufixos como _2, _3 normalmente são continuação, mas só use isso como indício.
A ordem será usada apenas como preferência GUIADA, nunca como prova visual.
Devolva cada filename exatamente uma vez. Se a ordem for ambígua, reduza confidence.

CONTEXTO DO PROJETO:
{json.dumps(project_context, ensure_ascii=False)}

CATÁLOGO:
{json.dumps(compact, ensure_ascii=False)}

FILENAMES OBRIGATÓRIOS:
{json.dumps(filenames, ensure_ascii=False)}
"""
    result, model = _generate_with_fallback(
        [prompt], ChronologyInference, max_output_tokens=6000, prefer_precision=False
    )
    # Fail safely if the model drops/duplicates a source.
    if len(result.ordered_filenames) != len(filenames) or set(result.ordered_filenames) != set(filenames):
        result.ordered_filenames = filenames
        result.confidence = min(result.confidence, 0.25)
        result.rationale = "Inferência inválida/incompleta; ordem existente preservada."
    return result, model
