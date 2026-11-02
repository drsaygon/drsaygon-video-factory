from __future__ import annotations

import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

from app.config import (
    ROUGH_CUT_QA_CHUNK_SECONDS,
    ROUGH_CUT_QA_ENABLED,
    ROUGH_CUT_QA_THRESHOLD,
    SYNC_CRITICAL_THRESHOLD,
    SYNC_SEMANTIC_THRESHOLD,
)
from app.models import (
    EditPlan,
    NarrationBeat,
    ProfessionalSyncBeat,
    ProfessionalSyncPlan,
    ProjectKnowledge,
    ProjectSettings,
    RoughCutIssue,
    RoughCutReview,
    SyncCandidate,
    SyncMatch,
)
from app.services.ffmpeg import audio_duration, create_qa_slice, create_verification_proxy, probe_media
from app.services.directed_evidence import search_directed_evidence
from app.services.continuity import apply_conservative_autopilot_policy, build_visual_runs
from app.services.draft_qa import render_rough_cut
from app.services.gemini import align_narration_audio, create_sync_matches, review_rough_cut, verify_visual_event
from app.services.project import ProjectPaths, now_iso, read_json, write_json


class ProfessionalSyncError(RuntimeError):
    pass


HARD_PHENOMENON_TERMS = (
    "faixa", "banding", "flicker", "faixa escura", "faixa preta",
    "pisca", "piscando", "cintil", "rolling band",
)
CONTEXTUAL_TERMS = (
    "comutacao", "comuta", "bateria", "rede eletrica", "alimentacao",
    "nobreak", "lampada", "luz", "iluminacao", "camera", "rolling shutter",
)
ABSTRACT_TERMS = (
    "sensor", "linha por linha", "instantes", "oscilacao", "frequencia",
    "explicacao", "acontece porque", "leitura da camera", "forma como",
)

_HARD_ACTION_PATTERNS = (
    re.compile(r"\b(?:tiro|retiro|retirando|tirando|desconecto|desconectando|removo|removendo)\b.{0,48}\b(?:tomada|plugue|cabo)\b"),
    re.compile(r"\b(?:coloco|conecto|conectando|encaixo|encaixando|insiro|inserindo)\b.{0,48}\b(?:tomada|plugue|cabo)\b"),
    re.compile(r"\b(?:aperto|apertando|pressiono|pressionando|aciono|acionando)\b.{0,36}\b(?:botao|interruptor)\b"),
    re.compile(r"\b(?:acende|acendem|acendeu|apaga|apagam|apagou)\b"),
    re.compile(r"\b(?:ligo|ligando|desligo|desligando)\b.{0,36}\b(?:nobreak|equipamento|luz|lampada|iluminacao)\b"),
)


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()


def _visual_obligation(text: str) -> str:
    """Classify how literally the picture must prove the spoken phrase.

    v6.0 over-classified words such as "muda", "depois", "resultado" and
    "comutacao" as hard evidence. That made Autopilot spend minutes searching
    for a literal shot for explanatory phrases and then fail closed. A human
    editor does not require every sentence to introduce a new visual proof.
    """
    value = _plain(text)
    if any(term in value for term in HARD_PHENOMENON_TERMS):
        return "hard"
    if any(pattern.search(value) for pattern in _HARD_ACTION_PATTERNS):
        return "hard"
    if any(term in value for term in ABSTRACT_TERMS):
        return "abstract"
    if any(term in value for term in CONTEXTUAL_TERMS):
        return "contextual"
    return "contextual"


def _is_critical(text: str) -> bool:
    return _visual_obligation(text) == "hard"


def _split_long_clause(clause: str) -> list[str]:
    clause = " ".join(clause.split()).strip()
    if not clause:
        return []

    # Visual-action markers deserve their own beat even in a short sentence.
    # This prevents a single scene from being asked to show both "tirando da
    # tomada" and a later "faixa escura", for example.
    marker = re.compile(
        r"(?i)(?=\b(?:quando|enquanto|depois|agora|nesse momento|exatamente quando|"
        r"aparece|surge|o resultado|mas|e entao|e ai|logo depois|e ele|e ela|e isso)\b)"
    )
    raw_parts: list[str] = []
    for comma_part in re.split(r"(?<=,)\s+", clause):
        pieces = [x.strip() for x in marker.split(comma_part) if x.strip()]
        raw_parts.extend(pieces or [comma_part.strip()])

    # Keep meaningful phrases together, but cap the semantic scope of critical
    # narration so one beat maps to one observable visual idea.
    pieces: list[str] = []
    for part in raw_parts:
        words = part.split()
        if len(words) <= 18:
            pieces.append(part)
            continue
        current: list[str] = []
        for word in words:
            current.append(word)
            if len(current) >= 16:
                pieces.append(" ".join(current))
                current = []
        if current:
            if pieces and len(current) <= 2:
                pieces[-1] = f"{pieces[-1]} {' '.join(current)}".strip()
            else:
                pieces.append(" ".join(current))

    # Merge tiny lead-ins ("Olha isso", "Repara") with the next phrase unless
    # they are the only beat.
    merged: list[str] = []
    for part in pieces:
        if merged and len(part.split()) <= 2:
            merged[-1] = f"{merged[-1]} {part}".strip()
        elif not merged and len(part.split()) <= 2:
            merged.append(part)
        else:
            if merged and len(merged[-1].split()) <= 2:
                merged[-1] = f"{merged[-1]} {part}".strip()
            else:
                merged.append(part)
    return merged or [clause]


def split_narration_into_beats(text: str) -> list[str]:
    text = " ".join((text or "").split()).strip()
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?;:])\s+", text)
    pieces: list[str] = []
    for sentence in sentences:
        pieces.extend(_split_long_clause(sentence))
    pieces = [piece.strip() for piece in pieces if piece.strip()]
    merged: list[str] = []
    pending = ""
    for piece in pieces:
        if len(piece.split()) <= 2:
            if pending:
                pending = f"{pending} {piece}".strip()
            else:
                pending = piece
            continue
        if pending:
            piece = f"{pending} {piece}".strip()
            pending = ""
        merged.append(piece)
    if pending:
        if merged:
            merged[-1] = f"{merged[-1]} {pending}".strip()
        else:
            merged.append(pending)
    return merged or [text]


def _weighted_durations(texts: list[str], total: float) -> list[float]:
    if not texts:
        return []
    if len(texts) == 1:
        return [total]
    weights = [max(2.0, float(len(re.findall(r"\w+", t)))) for t in texts]
    min_each = min(0.85, max(0.25, total / (len(texts) * 2.2)))
    fixed = min_each * len(texts)
    remainder = max(0.0, total - fixed)
    total_weight = sum(weights) or 1.0
    durations = [min_each + remainder * (w / total_weight) for w in weights]
    # Exact total after floating-point math.
    durations[-1] += total - sum(durations)
    return durations


def _aligned_visual_intervals(
    timings: list[tuple[float, float, float]],
    duration: float,
) -> list[tuple[float, float, float]]:
    """Partition the entire narration using real speech transitions as boundaries."""
    if not timings:
        return []
    if len(timings) == 1:
        return [(0.0, duration, timings[0][2])]
    boundaries = [0.0]
    for index in range(1, len(timings)):
        previous = timings[index - 1]
        current = timings[index]
        midpoint = (previous[1] + current[0]) / 2.0
        # Keep intervals monotonic even if timestamp estimates slightly overlap.
        min_boundary = boundaries[-1] + 0.18
        remaining = len(timings) - index
        max_boundary = duration - (remaining * 0.18)
        boundaries.append(max(min_boundary, min(midpoint, max_boundary)))
    boundaries.append(duration)
    return [
        (boundaries[i], boundaries[i + 1], timings[i][2])
        for i in range(len(timings))
    ]


def build_narration_beats(
    paths: ProjectPaths,
    plan: EditPlan,
    alignment_prompt: str,
    logger: Callable[[str], None],
    settings: ProjectSettings,
) -> list[NarrationBeat]:
    beats: list[NarrationBeat] = []
    global_cursor = 0.0
    for block in plan.blocks:
        if settings.audio_mode == "original":
            # v6 Original Audio mode follows EDITED SOURCE CLIPS, not sentences.
            # One chosen continuous clip is one audio/visual unit. This prevents
            # dialogue, reactions and family/vlog footage from being re-cut just
            # because the semantic guide contains another phrase.
            clip_specs = list(block.clips)
            if not clip_specs:
                raise ProfessionalSyncError(f"Bloco {block.number} sem tomada para audio original")
            beat_specs = []
            intervals = []
            cursor = 0.0
            for idx, clip in enumerate(clip_specs, start=1):
                clip_duration = max(0.30, float(clip.end_seconds - clip.start_seconds))
                guide = " ".join(
                    part.strip() for part in (block.title, clip.reason, block.editing_note) if str(part).strip()
                )
                beat_specs.append({
                    "beat_id": f"b{block.number:03d}_v{idx:02d}",
                    "text": guide or f"Trecho original {idx} do bloco {block.number}",
                })
                intervals.append((cursor, cursor + clip_duration, 1.0))
                cursor += clip_duration
            duration = cursor
        elif block.needs_voice:
            recording = paths.narration / f"bloco_{block.number:03d}.wav"
            if not recording.exists():
                raise ProfessionalSyncError(
                    f"Falta a narracao aprovada do bloco {block.number}."
                )
            duration = audio_duration(recording)
            if duration < 0.3:
                raise ProfessionalSyncError(f"Narracao do bloco {block.number} curta demais")
            texts = split_narration_into_beats(block.narration)
            beat_specs = [
                {"beat_id": f"b{block.number:03d}_v{idx:02d}", "text": text}
                for idx, text in enumerate(texts, start=1)
            ]

            if len(beat_specs) == 1:
                intervals = [(0.0, duration, 1.0)]
            else:
                try:
                    alignment, model = align_narration_audio(
                        recording, beat_specs, duration, alignment_prompt, logger
                    )
                except Exception as exc:
                    raise ProfessionalSyncError(
                        f"Nao foi possivel alinhar a fala real do bloco {block.number}: {exc}"
                    ) from exc
                logger(f"Alinhamento real da narracao do bloco {block.number} criado com {model}")
                by_id = {item.beat_id: item for item in alignment.timings}
                expected_ids = [item["beat_id"] for item in beat_specs]
                if set(by_id) != set(expected_ids) or len(by_id) != len(expected_ids):
                    raise ProfessionalSyncError(
                        f"O alinhador de audio do bloco {block.number} nao devolveu exatamente as batidas esperadas"
                    )
                ordered: list[tuple[float, float, float]] = []
                previous_start = -1.0
                for beat_id in expected_ids:
                    item = by_id[beat_id]
                    start = max(0.0, min(float(item.speech_start_seconds), duration))
                    end = max(start + 0.03, min(float(item.speech_end_seconds), duration))
                    if start + 0.20 < previous_start:
                        raise ProfessionalSyncError(
                            f"Alinhamento fora de ordem no bloco {block.number} ({beat_id})"
                        )
                    if item.confidence < 0.52:
                        raise ProfessionalSyncError(
                            f"Baixa confianca ({item.confidence:.0%}) ao localizar a fala '{beat_id}' no bloco {block.number}"
                        )
                    ordered.append((start, end, float(item.confidence)))
                    previous_start = start
                intervals = _aligned_visual_intervals(ordered, duration)
        else:
            duration = max(0.5, block.estimated_seconds)
            texts = [f"Demonstração visual: {block.title}. {block.editing_note}".strip()]
            beat_specs = [{"beat_id": f"b{block.number:03d}_v01", "text": texts[0]}]
            intervals = [(0.0, duration, 1.0)]

        for idx, (spec, interval) in enumerate(zip(beat_specs, intervals), start=1):
            local_start, local_end, confidence = interval
            text = spec["text"]
            beats.append(NarrationBeat(
                beat_id=spec["beat_id"],
                block_number=block.number,
                beat_number=idx,
                text=text,
                output_start_seconds=round(global_cursor + local_start, 3),
                output_end_seconds=round(global_cursor + local_end, 3),
                required_observation=text,
                critical=_is_critical(text),
                visual_obligation=_visual_obligation(text),
                timing_source="audio_aligned" if (block.needs_voice and settings.audio_mode != "original") else "estimated",
                timing_confidence=round(confidence, 3),
            ))
        global_cursor += duration
    return beats


def flatten_evidence(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for video in catalog.get("videos", []):
        filename = video.get("filename", "")
        sequence_index = int(video.get("sequence_index", 0))
        for index, segment in enumerate(video.get("segments", []), start=1):
            if not segment.get("use", True):
                continue
            event_id = segment.get("event_id") or f"legacy_{len(evidence)+1:04d}"
            evidence.append({
                "event_id": event_id,
                "filename": filename,
                "sequence_index": sequence_index,
                "start_seconds": float(segment.get("start_seconds", 0)),
                "end_seconds": float(segment.get("end_seconds", 0)),
                "description": segment.get("description", ""),
                "event_kind": segment.get("event_kind", "broll"),
                "actions": segment.get("actions", []),
                "objects": segment.get("objects", []),
                "states": segment.get("states", []),
                "visual_phenomena": segment.get("visual_phenomena", []),
                "tags": segment.get("tags", []),
                "recommended_use": segment.get("recommended_use", ""),
                "evidence_timestamps": segment.get("evidence_timestamps", []),
                "confidence": float(segment.get("confidence", 0.65)),
                "critical_action": bool(segment.get("critical_action", False)),
                "verified_by_user": bool(segment.get("verified_by_user", False)),
                "detection_source": segment.get("detection_source", "gemini_general"),
                "focus_x": float(segment.get("focus_x", 0.5)),
                "focus_y": float(segment.get("focus_y", 0.5)),
                "vertical_strategy": segment.get("vertical_strategy", "crop"),
                "shot_style": segment.get("shot_style", "normal"),
                "original_audio_is_important": bool(segment.get("original_audio_is_important", False)),
            })
    return evidence


def catalog_is_professional(catalog: dict[str, Any]) -> bool:
    evidence = flatten_evidence(catalog)
    if not evidence:
        return False
    rich = sum(1 for item in evidence if item.get("event_id") and (
        item.get("actions") or item.get("states") or item.get("visual_phenomena")
    ))
    return rich >= max(1, math.ceil(len(evidence) * 0.25))


def _token_set(text: str) -> set[str]:
    stop = {
        "a", "o", "as", "os", "um", "uma", "de", "do", "da", "dos", "das", "e", "em",
        "no", "na", "nos", "nas", "que", "eu", "isso", "esse", "essa", "este", "esta", "para",
        "por", "com", "se", "mais", "como", "ao", "aqui", "ali", "ele", "ela",
    }
    return {w for w in re.findall(r"[a-z0-9]+", _plain(text)) if len(w) > 2 and w not in stop}


def _lexical_score(beat: NarrationBeat, item: dict[str, Any]) -> float:
    query = _token_set(beat.text)
    hay = " ".join([
        item.get("description", ""), item.get("recommended_use", ""),
        " ".join(item.get("actions", [])), " ".join(item.get("objects", [])),
        " ".join(item.get("states", [])), " ".join(item.get("visual_phenomena", [])),
        " ".join(item.get("tags", [])),
    ])
    tokens = _token_set(hay)
    if not query:
        return 0.0
    overlap = len(query & tokens) / max(1, len(query))
    bonus = 0.0
    beat_plain = _plain(beat.text)
    if "faixa" in beat_plain and any("faixa" in _plain(x) or "band" in _plain(x) for x in item.get("visual_phenomena", [])):
        bonus += 0.35
    if ("tomada" in beat_plain or "plugue" in beat_plain) and any(
        "tomada" in _plain(x) or "plug" in _plain(x) for x in item.get("actions", []) + item.get("objects", [])
    ):
        bonus += 0.28
    if beat.critical and item.get("critical_action"):
        bonus += 0.10
    return min(1.0, overlap + bonus)


def _fallback_candidates(beat: NarrationBeat, evidence: list[dict[str, Any]]) -> list[SyncCandidate]:
    scored: list[tuple[dict[str, Any], float]] = []
    beat_plain = _plain(beat.text)
    for item in evidence:
        score = _lexical_score(beat, item)
        phenomena = " ".join(item.get("visual_phenomena", []))
        if item.get("verified_by_user"):
            # Creator-confirmed evidence is ground truth for this project. It must
            # outrank uncertain model descriptions instead of being buried among
            # generic semantic candidates.
            score += 0.34
            if any(term in beat_plain for term in HARD_PHENOMENON_TERMS) and any(
                term in _plain(phenomena) for term in ("faixa", "band", "flicker", "pisca")
            ):
                score += 0.40
        elif item.get("detection_source") == "directed_search_verified":
            score += 0.16
        scored.append((item, min(1.0, score)))

    scored.sort(key=lambda pair: (pair[1], float(pair[0].get("confidence", 0))), reverse=True)
    return [
        SyncCandidate(
            event_id=item["event_id"],
            score=round(score, 3),
            reason=(
                "Evidência confirmada pelo autor"
                if item.get("verified_by_user")
                else "Recuperação lexical determinística"
            ),
        )
        for item, score in scored[:5] if score > 0.04
    ]


def _fit_source_window(item: dict[str, Any], target: float, footage: Path) -> tuple[float, float]:
    source = footage / item["filename"]
    info = probe_media(source)
    seg_start = max(0.0, min(float(item["start_seconds"]), info.duration))
    seg_end = max(seg_start + 0.05, min(float(item["end_seconds"]), info.duration))
    event_points = [
        float(x) for x in item.get("evidence_timestamps", [])
        if seg_start <= float(x) <= seg_end
    ]
    center = event_points[0] if event_points else (seg_start + seg_end) / 2.0
    wanted = max(0.35, target)
    if seg_end - seg_start >= wanted:
        start = max(seg_start, min(center - wanted * 0.42, seg_end - wanted))
        return round(start, 3), round(start + wanted, 3)
    # Expand around the evidence within the original file.
    start = max(0.0, center - wanted * 0.42)
    end = min(info.duration, start + wanted)
    start = max(0.0, end - wanted)
    return round(start, 3), round(end, 3)


def _event_phase_for_text(text: str) -> float:
    """Where inside the spoken beat the decisive visual event should land."""
    value = _plain(text).strip()
    if value.startswith(("agora", "olha", "repara", "presta atencao")):
        return 0.58
    if any(term in value for term in ("exatamente quando", "no momento em que", "quando eu", "quando ele", "quando ela")):
        return 0.56
    if value.startswith(("depois", "logo depois", "em seguida")):
        return 0.44
    return 0.50


def _fit_around_event(
    footage: Path, filename: str, event_center: float, target: float, phase: float
) -> tuple[float, float]:
    info = probe_media(footage / filename)
    wanted = max(0.35, float(target))
    phase = max(0.25, min(0.75, float(phase)))
    start = event_center - wanted * phase
    start = max(0.0, min(start, max(0.0, info.duration - wanted)))
    end = min(info.duration, start + wanted)
    start = max(0.0, end - wanted)
    return round(start, 3), round(end, 3)


def _candidate_map(match: SyncMatch | None, beat: NarrationBeat, evidence: list[dict[str, Any]]) -> list[SyncCandidate]:
    known = {item["event_id"] for item in evidence}
    by_id = {item["event_id"]: item for item in evidence}
    candidates = [] if match is None else [c for c in match.candidates if c.event_id in known]
    seen = {c.event_id for c in candidates}
    for candidate in _fallback_candidates(beat, evidence):
        if candidate.event_id not in seen:
            candidates.append(candidate)
            seen.add(candidate.event_id)

    def rank(candidate: SyncCandidate) -> tuple[float, float, float]:
        item = by_id.get(candidate.event_id, {})
        creator_truth = 1.0 if item.get("verified_by_user") else 0.0
        directed_truth = 1.0 if item.get("detection_source") == "directed_search_verified" else 0.0
        return (
            float(candidate.score) + creator_truth * 0.42 + directed_truth * 0.14,
            creator_truth,
            float(item.get("confidence", 0.0)),
        )

    return sorted(candidates, key=rank, reverse=True)[:6]


def _safe_context_candidate(
    beat: NarrationBeat,
    evidence: list[dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
    last_event_id: str,
    preferred_filenames: list[str],
) -> SyncCandidate | None:
    """Choose safe B-roll for explanatory/contextual narration without failing.

    A professional editor does not require literal proof for every explanatory
    sentence. Prefer the current sustained shot, then creator-confirmed evidence,
    then a high-confidence hold/demo from the same editorial block.
    """
    if last_event_id:
        item = evidence_by_id.get(last_event_id)
        if item and item.get("shot_style") in {"hold", "demo"}:
            return SyncCandidate(
                event_id=last_event_id,
                score=max(0.62, float(item.get("confidence", 0.7))),
                reason="Continuidade automática: manter a tomada já estabelecida",
            )

    preferred = set(preferred_filenames)
    ranked: list[tuple[dict[str, Any], float]] = []
    for item in evidence:
        score = _lexical_score(beat, item)
        if item.get("verified_by_user"):
            score += 0.20
        if item.get("filename") in preferred:
            score += 0.22
        if item.get("shot_style") in {"hold", "demo"}:
            score += 0.16
        score += min(0.12, float(item.get("confidence", 0.0)) * 0.12)
        ranked.append((item, score))
    if not ranked:
        return None
    item, score = max(ranked, key=lambda pair: pair[1])
    return SyncCandidate(
        event_id=item["event_id"],
        score=max(0.60, min(0.90, score)),
        reason="B-roll contextual automático; nenhuma prova literal era necessária",
    )


def _consolidate_noncritical_continuity(
    beats: list[ProfessionalSyncBeat],
    evidence_by_id: dict[str, dict[str, Any]],
    footage: Path,
) -> None:
    """Make consecutive sustained beats use one continuous source passage."""
    index = 0
    while index < len(beats):
        first = beats[index]
        if first.critical or first.verification == "failed" or not first.event_id:
            index += 1
            continue
        end_index = index + 1
        while end_index < len(beats):
            nxt = beats[end_index]
            if (
                nxt.critical
                or nxt.verification == "failed"
                or nxt.event_id != first.event_id
                or nxt.block_number != first.block_number
            ):
                break
            end_index += 1
        run = beats[index:end_index]
        if len(run) >= 2:
            item = evidence_by_id.get(first.event_id)
            if item and item.get("shot_style") in {"hold", "demo"}:
                total = sum(beat.duration for beat in run)
                source_start, source_end = _fit_source_window(item, total, footage)
                if source_end - source_start + 0.08 >= total:
                    cursor = source_start
                    for beat in run:
                        beat.source_start_seconds = round(cursor, 3)
                        cursor += beat.duration
                        beat.source_end_seconds = round(cursor, 3)
                        beat.match_reason = f"{beat.match_reason} | Continuidade preservada".strip(" |")
        index = max(end_index, index + 1)


def _allows_chronology_backtrack(text: str) -> bool:
    value = _plain(text)
    return any(term in value for term in (
        "antes", "anteriormente", "voltando", "volta aqui", "de novo", "novamente",
        "repara de novo", "no começo", "primeiro eu", "como eu mostrei",
    ))


def _sequence_index(item: dict[str, Any]) -> int:
    try:
        return int(item.get("sequence_index", 0))
    except Exception:
        return 0


def _persist_directed_item(catalog: dict[str, Any], item: dict[str, Any]) -> None:
    video = next((v for v in catalog.get("videos", []) if v.get("filename") == item.get("filename")), None)
    if not video:
        return
    if any(seg.get("event_id") == item.get("event_id") for seg in video.get("segments", [])):
        return
    video.setdefault("segments", []).append({
        "start_seconds": item["start_seconds"],
        "end_seconds": item["end_seconds"],
        "description": item.get("description", "Evidência localizada por busca dirigida"),
        "quality": "excelente",
        "tags": ["busca dirigida", "evidência verificada"],
        "recommended_use": "Evidência localizada especificamente para uma batida crítica.",
        "use": True,
        "original_audio_is_important": False,
        "focus_x": item.get("focus_x", 0.5),
        "focus_y": item.get("focus_y", 0.5),
        "vertical_strategy": item.get("vertical_strategy", "crop"),
        "shot_style": item.get("shot_style", "demo"),
        "min_take_seconds": 0.5,
        "max_take_seconds": max(1.0, float(item["end_seconds"]) - float(item["start_seconds"])),
        "event_id": item["event_id"],
        "event_kind": item.get("event_kind", "phenomenon"),
        "actions": item.get("actions", []),
        "objects": item.get("objects", []),
        "states": item.get("states", []),
        "visual_phenomena": item.get("visual_phenomena", []),
        "evidence_timestamps": item.get("evidence_timestamps", []),
        "confidence": item.get("confidence", 0.85),
        "critical_action": True,
        "verified_by_user": False,
        "detection_source": "directed_search_verified",
    })
    video["segments"] = sorted(video["segments"], key=lambda x: (float(x.get("start_seconds", 0)), str(x.get("event_id", ""))))


def review_global_rough_cut(
    paths: ProjectPaths,
    draft: Path,
    sync: ProfessionalSyncPlan,
    knowledge: ProjectKnowledge,
    rough_review_prompt: str,
    logger: Callable[[str], None],
) -> RoughCutReview:
    """Review the actual draft in bounded chunks for Shorts AND long-form video.

    Per-beat verification catches wrong evidence in isolation. This pass catches
    editorial errors that only exist in the assembled timeline. Chunking keeps a
    10+ minute video practical and avoids one enormous multimodal request.
    """
    info = probe_media(draft)
    duration = max(0.01, info.duration)
    chunk_seconds = max(25.0, float(ROUGH_CUT_QA_CHUNK_SECONDS))
    windows: list[tuple[float, float]] = []
    cursor = 0.0
    while cursor < duration - 0.01:
        end = min(duration, cursor + chunk_seconds)
        windows.append((cursor, end))
        cursor = end

    all_issues: list[RoughCutIssue] = []
    confidences: list[float] = []
    summaries: list[str] = []
    approved = True
    qa_dir = paths.temp / "rough_cut_qa" / "chunks"
    qa_dir.mkdir(parents=True, exist_ok=True)

    for idx, (start, end) in enumerate(windows, start=1):
        local_beats: list[dict[str, Any]] = []
        for beat in sync.beats:
            if beat.output_end_seconds <= start or beat.output_start_seconds >= end:
                continue
            data = beat.model_dump(mode="json")
            data["output_start_seconds"] = round(max(0.0, beat.output_start_seconds - start), 3)
            data["output_end_seconds"] = round(min(end - start, beat.output_end_seconds - start), 3)
            local_beats.append(data)
        if not local_beats:
            continue
        clip = draft
        if len(windows) > 1:
            clip = qa_dir / f"qa_{idx:03d}.mp4"
            create_qa_slice(draft, clip, start, end)
        logger(f"Rough Cut QA {idx}/{len(windows)}: {start:.1f}-{end:.1f}s")
        review, model = review_rough_cut(
            clip,
            local_beats,
            knowledge.locked_facts,
            {
                "mode": knowledge.chronology_mode,
                "footage_order": knowledge.footage_order,
                "notes": knowledge.chronology_notes,
                "chunk_output_start_seconds": round(start, 3),
                "quality_guard": sync.edit_quality.model_dump(mode="json") if sync.edit_quality else None,
            },
            rough_review_prompt,
            logger,
        )
        logger(f"Rough Cut QA chunk {idx} com {model}: approved={review.approved} confidence={review.confidence:.2f}")
        approved = approved and review.approved
        confidences.append(float(review.confidence))
        if review.summary:
            summaries.append(f"Trecho {idx}: {review.summary}")
        for issue in review.issues:
            data = issue.model_dump(mode="json")
            data["output_start_seconds"] = round(start + float(issue.output_start_seconds), 3)
            data["output_end_seconds"] = round(start + float(issue.output_end_seconds), 3)
            all_issues.append(RoughCutIssue.model_validate(data))

    critical = [issue for issue in all_issues if issue.severity == "critical"]
    confidence = min(confidences) if confidences else 0.0
    if critical:
        approved = False
    return RoughCutReview(
        approved=approved,
        confidence=round(confidence, 3),
        summary=" | ".join(summaries)[:8000] if summaries else "Revisão global sem resumo.",
        issues=all_issues,
    )



def _build_original_audio_sync(
    paths: ProjectPaths,
    plan: EditPlan,
    catalog: dict[str, Any],
    settings: ProjectSettings,
    knowledge: ProjectKnowledge,
    rough_review_prompt: str,
    logger: Callable[[str], None],
    progress: Callable[[int, str], None],
) -> ProfessionalSyncPlan:
    """Deterministic edit path for projects whose story lives in camera audio.

    The AI still chooses useful clips when it creates the edit plan, but this
    stage does NOT rematch every phrase through the evidence engine. The selected
    clip itself is authoritative. That preserves dialogue, reactions, pauses and
    natural continuity in kids/family/vlog/interview/event footage.
    """
    catalog_by_file = {v.get("filename", ""): v for v in catalog.get("videos", [])}
    evidence_by_id: dict[str, dict[str, Any]] = {}
    result_beats: list[ProfessionalSyncBeat] = []
    cursor = 0.0
    sequence_lookup = {v.get("filename", ""): int(v.get("sequence_index", 0)) for v in catalog.get("videos", [])}

    progress(18, "Áudio original: preservando tomadas e falas escolhidas no roteiro")
    for block in plan.blocks:
        for clip_index, clip in enumerate(block.clips, start=1):
            video = catalog_by_file.get(clip.filename)
            if not video:
                raise ProfessionalSyncError(f"Filmagem do roteiro nao existe no catalogo: {clip.filename}")
            source = paths.footage / clip.filename
            if not source.exists():
                raise ProfessionalSyncError(f"Filmagem original nao encontrada: {clip.filename}")
            info = probe_media(source)
            source_start = max(0.0, min(float(clip.start_seconds), info.duration))
            source_end = max(source_start + 0.05, min(float(clip.end_seconds), info.duration))
            if source_end - source_start < 0.25:
                continue
            duration = source_end - source_start

            # Recover the strongest overlapping catalog description/audio flag.
            best_segment: dict[str, Any] | None = None
            best_overlap = 0.0
            for segment in video.get("segments", []):
                overlap = max(
                    0.0,
                    min(source_end, float(segment.get("end_seconds", 0)))
                    - max(source_start, float(segment.get("start_seconds", 0))),
                )
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_segment = segment
            segment = best_segment or {}
            event_id = f"original_b{block.number:03d}_{clip_index:02d}"
            description = str(segment.get("description") or clip.reason or block.title).strip()
            audio_strategy = clip.audio_strategy
            # Global Original Audio mode means the camera soundtrack is the
            # primary program audio. Start every non-muted selected take at full
            # level; the creator can still change any VisualRun to ambient/mute
            # in step 6 and that override is authoritative during assembly.
            if audio_strategy != "mute":
                audio_strategy = "feature"
            evidence_by_id[event_id] = {
                "event_id": event_id,
                "filename": clip.filename,
                "sequence_index": sequence_lookup.get(clip.filename, 0),
                "start_seconds": source_start,
                "end_seconds": source_end,
                "description": description,
                "event_kind": segment.get("event_kind", "demo"),
                "actions": segment.get("actions", []),
                "objects": segment.get("objects", []),
                "states": segment.get("states", []),
                "visual_phenomena": segment.get("visual_phenomena", []),
                "confidence": max(0.85, float(segment.get("confidence", 0.85))),
                "verified_by_user": bool(segment.get("verified_by_user", False)),
                "shot_style": clip.shot_style,
                "focus_x": clip.focus_x,
                "focus_y": clip.focus_y,
                "vertical_strategy": clip.vertical_strategy,
                "original_audio_is_important": audio_strategy == "feature",
            }
            result_beats.append(ProfessionalSyncBeat(
                beat_id=f"b{block.number:03d}_v{clip_index:02d}",
                block_number=block.number,
                beat_number=clip_index,
                text=description or block.title,
                output_start_seconds=round(cursor, 3),
                output_end_seconds=round(cursor + duration, 3),
                required_observation=description or block.title,
                # Structural lock: in Original Audio mode the selected camera
                # clip/audio is the content itself. Continuity repair must never
                # replace it with another B-roll just to reduce a cut count.
                critical=True,
                visual_obligation="hard",
                timing_source="estimated",
                timing_confidence=1.0,
                event_id=event_id,
                filename=clip.filename,
                source_start_seconds=round(source_start, 3),
                source_end_seconds=round(source_end, 3),
                evidence_description=description,
                match_reason="Modo Áudio Original v6: tomada escolhida no roteiro preservada sem rematch por frase.",
                match_confidence=max(0.85, float(segment.get("confidence", 0.85))),
                verification="manual" if segment.get("verified_by_user") else "semantic",
                vertical_strategy=clip.vertical_strategy,
                focus_x=clip.focus_x,
                focus_y=clip.focus_y,
                audio_strategy=audio_strategy,
                shot_style=clip.shot_style,
                sequence_index=sequence_lookup.get(clip.filename, 0),
            ))
            cursor += duration

    if not result_beats:
        raise ProfessionalSyncError("O roteiro nao possui tomadas utilizaveis para audio original")

    progress(70, "Áudio original: consolidando cenas contínuas sem microcortes")
    visual_runs, quality = build_visual_runs(
        result_beats,
        evidence_by_id,
        paths.footage,
        knowledge.editing_style,
        long_form=settings.format == "youtube_16_9",
    )
    warnings = list(quality.warnings)
    sync = ProfessionalSyncPlan(
        version="6.0-original-audio",
        total_duration_seconds=round(cursor, 3),
        generated_at=now_iso(),
        beats=result_beats,
        visual_runs=visual_runs,
        edit_quality=quality,
        warnings=warnings,
        ready=bool(visual_runs),
        evidence_mode="original_audio_plan",
        timing_mode="source_timeline",
        chronology_mode=knowledge.chronology_mode,
    )

    # The actual rough cut is still reviewed as a whole; this QA sees/hears the
    # original camera soundtrack, which is exactly what will be published.
    if sync.ready and ROUGH_CUT_QA_ENABLED:
        progress(90, "Renderizando rascunho com áudio original para QA")
        try:
            draft = render_rough_cut(paths, settings, plan, sync, logger)
            review = review_global_rough_cut(paths, draft, sync, knowledge, rough_review_prompt, logger)
            sync.rough_cut_qa = review
            critical = [issue for issue in review.issues if issue.severity == "critical"]
            if critical:
                sync.ready = False
                sync.warnings.append("QA encontrou problema crítico no rascunho com áudio original.")
            elif not review.approved:
                sync.warnings.append("QA editorial registrou ressalvas; o corte original permanece disponível para revisão rápida.")
        except Exception as exc:
            logger(f"QA de audio original indisponivel: {exc}")
            sync.warnings.append("QA multimodal do áudio original não pôde ser concluído; o corte determinístico foi preservado.")
    progress(100, "Corte com áudio original preparado")
    return sync

def build_professional_sync(
    paths: ProjectPaths,
    plan: EditPlan,
    catalog: dict[str, Any],
    sync_prompt: str,
    verify_prompt: str,
    alignment_prompt: str,
    logger: Callable[[str], None],
    progress: Callable[[int, str], None],
    *,
    settings: ProjectSettings,
    knowledge: ProjectKnowledge,
    directed_prompt: str,
    rough_review_prompt: str,
) -> ProfessionalSyncPlan:
    """Video Factory v6: evidence truth + source continuity + audio-aware editorial QA."""
    # Original-audio projects only need valid analyzed source ranges; dialogue,
    # family footage and vlogs must not be rejected merely because they contain
    # fewer "critical visual events" than a technical demonstration.
    if settings.audio_mode == "original":
        if not catalog.get("videos"):
            raise ProfessionalSyncError("Analise as filmagens antes de preparar o corte com audio original")
        return _build_original_audio_sync(
            paths, plan, catalog, settings, knowledge, rough_review_prompt, logger, progress
        )

    if not catalog_is_professional(catalog):
        raise ProfessionalSyncError(
            "O catalogo foi criado pela analise antiga. Va para 2. Analise e execute "
            "Analisar filmagens novamente para gerar evidencias temporais de alta precisao."
        )

    beats = build_narration_beats(paths, plan, alignment_prompt, logger, settings)
    evidence = flatten_evidence(catalog)
    if not beats or not evidence:
        raise ProfessionalSyncError("Nao ha narracao/evidencias suficientes para sincronizar")

    chronology_context = {
        "mode": knowledge.chronology_mode,
        "footage_order": knowledge.footage_order,
        "notes": knowledge.chronology_notes,
    }
    progress(8, "Quebrando a narracao em batidas visuais")
    matcher_payload = [beat.model_dump(mode="json") for beat in beats]
    compact_evidence = [{
        key: item.get(key) for key in (
            "event_id", "filename", "sequence_index", "start_seconds", "end_seconds", "description", "event_kind",
            "actions", "objects", "states", "visual_phenomena", "tags", "recommended_use",
            "confidence", "critical_action", "shot_style", "verified_by_user", "detection_source"
        )
    } for item in evidence]

    progress(18, "Casando cada frase apenas com evidencias existentes")
    match_plan, model = create_sync_matches(
        matcher_payload,
        compact_evidence,
        sync_prompt,
        chronology_context=chronology_context,
        locked_facts=knowledge.locked_facts,
    )
    logger(f"Mapa semantico de sincronismo criado com {model}")
    matches = {item.beat_id: item for item in match_plan.matches}
    evidence_by_id = {item["event_id"]: item for item in evidence}
    video_sequence = {v.get("filename", ""): int(v.get("sequence_index", 0)) for v in catalog.get("videos", [])}

    result_beats: list[ProfessionalSyncBeat] = []
    warnings: list[str] = []
    last_event_id = ""
    last_critical_sequence = -1
    critical_total = 0
    verified_count = 0

    def try_item(beat: NarrationBeat, candidate: SyncCandidate, item: dict[str, Any], critical: bool, required: str, candidate_index: int, *, allow_contextual_fallback: bool = False) -> ProfessionalSyncBeat | None:
        nonlocal verified_count
        duration = beat.output_end_seconds - beat.output_start_seconds
        source_start, source_end = _fit_source_window(item, duration, paths.footage)
        if source_end - source_start + 0.08 < duration:
            return None
        verification = "manual" if item.get("verified_by_user") else "semantic"
        confidence = min(float(candidate.score), float(item.get("confidence", 0.7)))
        reason = candidate.reason
        if item.get("verified_by_user"):
            confidence = 1.0
            reason = f"{reason} | Evidência confirmada pelo autor"
            if critical:
                verified_count += 1
        elif critical:
            progress_value = 20 + int((len(result_beats)) / max(1, len(beats)) * 55)
            progress(progress_value, f"Verificando evidência crítica {len(result_beats)+1}/{len(beats)}")
            check_dir = paths.temp / "sync_verify"
            check_dir.mkdir(parents=True, exist_ok=True)
            check_path = check_dir / f"{beat.beat_id}_c{candidate_index}.mp4"
            create_verification_proxy(paths.footage / item["filename"], check_path, source_start, source_end)
            try:
                check, verify_model = verify_visual_event(check_path, beat.text, required, verify_prompt, logger)
                logger(
                    f"Verificacao {beat.beat_id}/{candidate.event_id} com {verify_model}: "
                    f"confirmed={check.confirmed} confidence={check.confidence:.2f}"
                )
                if not check.confirmed or check.confidence < SYNC_CRITICAL_THRESHOLD:
                    return None
                verification = "verified"
                confidence = min(1.0, max(confidence, check.confidence))
                # The verifier can pinpoint where the decisive action/phenomenon
                # occurs inside the candidate clip. Re-center the FINAL source
                # window around that event so the image changes at the same
                # moment the critical phrase is spoken, rather than merely
                # somewhere inside the beat.
                if check.recommended_start_seconds is not None or check.recommended_end_seconds is not None:
                    rel_start = float(check.recommended_start_seconds or 0.0)
                    rel_end = float(check.recommended_end_seconds if check.recommended_end_seconds is not None else rel_start)
                    rel_start = max(0.0, min(rel_start, source_end - source_start))
                    rel_end = max(rel_start, min(rel_end, source_end - source_start))
                    event_center = source_start + (rel_start + rel_end) / 2.0
                    source_start, source_end = _fit_around_event(
                        paths.footage, item["filename"], event_center, duration, _event_phase_for_text(beat.text)
                    )
                reason = f"{candidate.reason} | Verificado no microclipe: {check.observed_evidence}"
                verified_count += 1
            except Exception as exc:
                logger(f"Falha ao verificar {beat.beat_id}/{candidate.event_id}: {exc}")
                return None
        elif confidence < SYNC_SEMANTIC_THRESHOLD:
            if not allow_contextual_fallback:
                return None
            # This is deliberately NOT treated as visual proof. It is safe
            # contextual coverage for narration that does not assert a literal
            # observable action/phenomenon.
            confidence = max(confidence, 0.58)
            reason = f"{reason} | cobertura contextual segura"

        return ProfessionalSyncBeat(
            beat_id=beat.beat_id,
            block_number=beat.block_number,
            beat_number=beat.beat_number,
            text=beat.text,
            output_start_seconds=beat.output_start_seconds,
            output_end_seconds=beat.output_end_seconds,
            required_observation=required,
            critical=critical,
            visual_obligation=beat.visual_obligation,
            timing_source=beat.timing_source,
            timing_confidence=beat.timing_confidence,
            event_id=item["event_id"],
            filename=item["filename"],
            source_start_seconds=source_start,
            source_end_seconds=source_end,
            evidence_description=item.get("description", ""),
            match_reason=reason,
            match_confidence=round(confidence, 3),
            verification=verification,
            vertical_strategy=item.get("vertical_strategy", "crop"),
            focus_x=float(item.get("focus_x", 0.5)),
            focus_y=float(item.get("focus_y", 0.5)),
            audio_strategy=(
                "mute" if settings.audio_mode == "narration"
                else ("feature" if item.get("original_audio_is_important") else "ambient")
            ),
            shot_style=item.get("shot_style", "normal"),
            sequence_index=_sequence_index(item),
        )

    block_preferred_files = {
        block.number: [clip.filename for clip in block.clips]
        for block in plan.blocks
    }

    for index, beat in enumerate(beats, start=1):
        match = matches.get(beat.beat_id)
        required = (match.required_observation if match and match.required_observation else beat.text).strip()
        obligation = _visual_obligation(beat.text)
        beat.visual_obligation = obligation
        beat.critical = obligation == "hard"
        critical = beat.critical
        if critical:
            critical_total += 1
        candidates = _candidate_map(match, beat, evidence)
        selected: ProfessionalSyncBeat | None = None

        # Chronology is now first-class metadata rather than accidental filename order.
        if candidates:
            def chronology_key(c: SyncCandidate) -> tuple[float, float]:
                item = evidence_by_id.get(c.event_id, {})
                seq = _sequence_index(item)
                bonus = 0.0
                if knowledge.chronology_mode != "off" and last_critical_sequence >= 0:
                    if seq >= last_critical_sequence:
                        bonus += 0.12
                    elif critical and not _allows_chronology_backtrack(beat.text):
                        bonus -= 0.35 if knowledge.chronology_mode == "strict" else 0.16
                return (float(c.score) + bonus, -float(seq))
            candidates = sorted(candidates, key=chronology_key, reverse=True)

        if not critical and last_event_id and candidates:
            previous_candidate = next((c for c in candidates if c.event_id == last_event_id), None)
            previous_item = evidence_by_id.get(last_event_id)
            if (
                previous_candidate and previous_item
                and previous_item.get("shot_style") in {"hold", "demo"}
                and previous_candidate.score >= candidates[0].score - 0.18
            ):
                candidates.remove(previous_candidate)
                candidates.insert(0, previous_candidate)

        for candidate_index, candidate in enumerate(candidates[:4], start=1):
            item = evidence_by_id.get(candidate.event_id)
            if not item:
                continue
            seq = _sequence_index(item)
            if (
                critical and knowledge.chronology_mode == "strict" and last_critical_sequence >= 0
                and seq < last_critical_sequence and not _allows_chronology_backtrack(beat.text)
            ):
                logger(f"Candidato {candidate.event_id} rejeitado por voltar na cronologia ({seq} < {last_critical_sequence})")
                continue
            selected = try_item(beat, candidate, item, critical, required, candidate_index)
            if selected:
                break

        # Explanatory/contextual narration must never stop Autopilot merely
        # because no clip literally "proves" the sentence. Hold the established
        # demonstration shot or use safe related B-roll.
        if selected is None and not critical:
            fallback = _safe_context_candidate(
                beat,
                evidence,
                evidence_by_id,
                last_event_id,
                block_preferred_files.get(beat.block_number, []),
            )
            if fallback:
                item = evidence_by_id.get(fallback.event_id)
                if item:
                    selected = try_item(
                        beat, fallback, item, False, required, 80,
                        allow_contextual_fallback=True,
                    )
                    if selected:
                        selected.visual_obligation = obligation
                        logger(
                            f"Autopilot contextual: {beat.beat_id} coberta por {selected.event_id} "
                            "sem exigir prova literal nem busca dirigida."
                        )

        # Automatic rescue is reserved for TRUE hard visual obligations.
        # search specifically for that observation using slowed clips and a local
        # banding/flicker pre-detector before giving up.
        if selected is None and critical:
            rescue_floor = 22 + int((index - 1) / max(1, len(beats)) * 52)
            progress(rescue_floor, f"Resgate automático de evidência essencial: {beat.beat_id}")
            try:
                candidate_files = [
                    evidence_by_id[c.event_id].get("filename", "")
                    for c in candidates[:4]
                    if c.event_id in evidence_by_id
                ]
                candidate_files.extend(block_preferred_files.get(beat.block_number, []))
                candidate_files = [name for name in dict.fromkeys(candidate_files) if name]
                # Keep expensive rescue narrow. If semantic ranking already
                # identified likely files, search those first instead of scanning
                # the whole project twelve windows at a time.
                search_files = candidate_files[:3] or None
                beat_floor = rescue_floor
                beat_ceiling = 22 + int(index / max(1, len(beats)) * 52)
                directed = search_directed_evidence(
                    paths,
                    catalog,
                    required,
                    directed_prompt,
                    logger,
                    filenames=search_files,
                    max_candidates=2,
                    progress=lambda pct, msg, lo=beat_floor, hi=beat_ceiling: progress(
                        max(lo, min(hi, lo + int((hi - lo) * max(0, min(100, pct)) / 100))),
                        f"Resgate automático {index}/{len(beats)} · {msg}",
                    ),
                )
                for d_index, found in enumerate(directed.candidates, start=1):
                    seq = video_sequence.get(found.filename, 0)
                    if (
                        knowledge.chronology_mode == "strict" and last_critical_sequence >= 0
                        and seq < last_critical_sequence and not _allows_chronology_backtrack(beat.text)
                    ):
                        continue
                    event_id = f"dir_{beat.beat_id}_{d_index:02d}"
                    item = {
                        "event_id": event_id,
                        "filename": found.filename,
                        "sequence_index": seq,
                        "start_seconds": found.source_start_seconds,
                        "end_seconds": found.source_end_seconds,
                        "description": found.observed_evidence or f"Evidência dirigida para {required}",
                        "event_kind": "phenomenon" if found.visual_phenomena else "action",
                        "actions": found.actions,
                        "objects": [],
                        "states": found.states,
                        "visual_phenomena": found.visual_phenomena,
                        "tags": ["busca dirigida"],
                        "recommended_use": f"Evidência localizada especificamente para: {required}",
                        "evidence_timestamps": [round((found.source_start_seconds + found.source_end_seconds) / 2, 3)],
                        "confidence": found.confidence,
                        "critical_action": True,
                        "focus_x": 0.5,
                        "focus_y": 0.5,
                        "vertical_strategy": "crop",
                        "shot_style": "demo",
                        "original_audio_is_important": False,
                        "verified_by_user": False,
                        "detection_source": found.detection_source,
                    }
                    candidate = SyncCandidate(event_id=event_id, score=found.confidence, reason="Resgate visual automático v6")
                    selected = try_item(beat, candidate, item, True, required, 90 + d_index)
                    if selected:
                        evidence.append(item)
                        evidence_by_id[event_id] = item
                        _persist_directed_item(catalog, item)
                        write_json(paths.analysis / "catalogo_cenas.json", catalog)
                        logger(f"Evidência dirigida promovida ao catálogo: {event_id}")
                        break
            except Exception as exc:
                logger(f"Busca dirigida não conseguiu resgatar {beat.beat_id}: {exc}")

        if selected is None:
            message = (
                f"Sem evidência visual comprovada para: '{beat.text}'"
                if critical else f"Sem tomada com confiança suficiente para: '{beat.text}'"
            )
            warnings.append(message)
            result_beats.append(ProfessionalSyncBeat(
                beat_id=beat.beat_id,
                block_number=beat.block_number,
                beat_number=beat.beat_number,
                text=beat.text,
                output_start_seconds=beat.output_start_seconds,
                output_end_seconds=beat.output_end_seconds,
                required_observation=required,
                critical=critical,
                visual_obligation=obligation,
                timing_source=beat.timing_source,
                timing_confidence=beat.timing_confidence,
                match_reason=message,
                verification="failed",
            ))
        else:
            result_beats.append(selected)
            last_event_id = selected.event_id
            if critical:
                last_critical_sequence = max(last_critical_sequence, selected.sequence_index)

    # v5: semantic beats are evidence units, NOT cut points. The Continuity
    # Engine turns them into a much smaller set of real visual runs.
    _consolidate_noncritical_continuity(result_beats, evidence_by_id, paths.footage)
    progress(84, "Otimizando continuidade e removendo cortes artificiais")
    visual_runs, quality = build_visual_runs(
        result_beats, evidence_by_id, paths.footage, knowledge.editing_style,
        long_form=settings.format == "youtube_16_9"
    )
    for repair in quality.repairs:
        logger(f"Continuity Engine: {repair}")
    for warning in quality.warnings:
        logger(f"Edit Quality Guard: {warning}")

    progress(88, "Executando gate anti-alucinacao e qualidade editorial")
    failed_any = [b for b in result_beats if b.verification == "failed"]
    # v6: missing visual truth can still block Autopilot. Structural rhythm is
    # an editor responsibility, so Quality Guard diagnoses/repairs but never sends
    # the creator back to the timeline. If a deterministic warning survives, the
    # actual rough cut MUST be judged by the adversarial global critic.
    ready = not failed_any and bool(visual_runs)
    if not quality.passed:
        warnings.append(
            "Quality Guard v6 encontrou uma excecao estrutural residual apos autocorrecoes. "
            "O Autopilot continuou automaticamente para validar o RASCUNHO REAL no critico global."
        )
        warnings.extend(f"QUALIDADE: {item}" for item in quality.warnings)
    if critical_total and verified_count < critical_total:
        warnings.append(f"{verified_count}/{critical_total} batidas críticas foram confirmadas visualmente por verificador independente ou pelo autor.")

    total_duration = max((b.output_end_seconds for b in result_beats), default=0.0)
    sync = ProfessionalSyncPlan(
        total_duration_seconds=round(total_duration, 3),
        generated_at=now_iso(),
        beats=result_beats,
        visual_runs=visual_runs,
        edit_quality=quality,
        warnings=warnings,
        ready=ready,
        chronology_mode=knowledge.chronology_mode,
    )

    # Global critic sees the actual low-res edit with narration, catching errors
    # that per-clip verification cannot: scene too early/late, causal order, wrong
    # state at the exact spoken moment, or bad vertical framing.
    if ready and ROUGH_CUT_QA_ENABLED:
        progress(92, "Renderizando rascunho para revisão editorial independente")
        try:
            draft = render_rough_cut(paths, settings, plan, sync, logger)
            review = review_global_rough_cut(
                paths, draft, sync, knowledge, rough_review_prompt, logger
            )
            sync.rough_cut_qa = review
            logger(f"Rough Cut QA global: approved={review.approved} confidence={review.confidence:.2f}")
            critical_issues = [issue for issue in review.issues if issue.severity == "critical"]
            needs_retry = bool(
                (not review.approved or review.confidence < ROUGH_CUT_QA_THRESHOLD)
                and not critical_issues
            )
            if needs_retry:
                # v6 Autopilot retries one conservative edit automatically for
                # non-critical editorial complaints. The creator is not the repair loop.
                progress(95, "Critico pediu ajuste editorial; aplicando Safe Cut automaticamente")
                retry_repairs = apply_conservative_autopilot_policy(result_beats, evidence_by_id)
                if retry_repairs:
                    for repair in retry_repairs:
                        logger(f"Autopilot QA AutoRepair: {repair}")
                    visual_runs, quality = build_visual_runs(
                        result_beats, evidence_by_id, paths.footage, "demonstrative",
                        long_form=settings.format == "youtube_16_9"
                    )
                    sync.visual_runs = visual_runs
                    sync.edit_quality = quality
                    progress(96, "Renderizando segunda versao do rascunho apos autocorrecao")
                    draft = render_rough_cut(paths, settings, plan, sync, logger)
                    review = review_global_rough_cut(
                        paths, draft, sync, knowledge, rough_review_prompt, logger
                    )
                    sync.rough_cut_qa = review
                    critical_issues = [issue for issue in review.issues if issue.severity == "critical"]
                    logger(
                        f"Rough Cut QA retry: approved={review.approved} "
                        f"confidence={review.confidence:.2f}"
                    )
            if not review.approved or critical_issues or review.confidence < ROUGH_CUT_QA_THRESHOLD:
                sync.ready = False
                warnings.append(
                    "O critico global ainda encontrou um problema editorial depois da tentativa automatica de Safe Cut."
                )
                for issue in review.issues[:8]:
                    warnings.append(f"QA {issue.severity}: {issue.description}")
        except Exception as exc:
            warnings.append(f"Rough Cut QA indisponível nesta execução: {exc}")
            logger(f"Rough Cut QA falhou sem bloquear o mapa já verificado: {exc}")

    sync.warnings = warnings
    write_json(paths.analysis / "sincronismo_profissional.json", sync.model_dump(mode="json"))
    progress(100, "Corte profissional aprovado" if sync.ready else "Corte profissional precisa de revisão")
    return sync

def recheck_existing_sync(
    paths: ProjectPaths,
    settings: ProjectSettings,
    plan: EditPlan,
    sync: ProfessionalSyncPlan,
    knowledge: ProjectKnowledge,
    rough_review_prompt: str,
    logger: Callable[[str], None],
) -> ProfessionalSyncPlan:
    """Re-run only the global editorial QA after a human changes one shot."""
    sync.warnings = [
        w for w in sync.warnings
        if not (w.startswith("QA ") or "crítico global" in w or "Rough Cut QA" in w or "revisão global" in w)
    ]
    base_ready = all(beat.verification != "failed" and beat.filename for beat in sync.beats)
    if not base_ready:
        sync.ready = False
        sync.rough_cut_qa = None
        write_json(paths.analysis / "sincronismo_profissional.json", sync.model_dump(mode="json"))
        return sync

    # A manual shot change invalidates the actual cut structure. Rebuild visual
    # runs before QA; otherwise a human override could reintroduce the v4
    # one-beat-one-cut behavior.
    catalog = read_json(paths.analysis / "catalogo_cenas.json") or {}
    evidence_by_id = {item["event_id"]: item for item in flatten_evidence(catalog)}
    sync.visual_runs, sync.edit_quality = build_visual_runs(
        sync.beats, evidence_by_id, paths.footage, knowledge.editing_style,
        long_form=settings.format == "youtube_16_9"
    )
    sync.version = "6.0"
    if not sync.edit_quality.passed:
        sync.warnings.extend(f"QUALIDADE: {w}" for w in sync.edit_quality.warnings)
        sync.warnings.append(
            "Quality Guard residual encaminhado automaticamente ao critico do rascunho; nenhuma acao manual foi exigida."
        )
    if not ROUGH_CUT_QA_ENABLED:
        sync.ready = True
        write_json(paths.analysis / "sincronismo_profissional.json", sync.model_dump(mode="json"))
        return sync

    draft = render_rough_cut(paths, settings, plan, sync, logger)
    review = review_global_rough_cut(paths, draft, sync, knowledge, rough_review_prompt, logger)
    sync.rough_cut_qa = review
    critical_issues = [issue for issue in review.issues if issue.severity == "critical"]
    sync.ready = bool(review.approved and not critical_issues and review.confidence >= ROUGH_CUT_QA_THRESHOLD)
    if not sync.ready:
        sync.warnings.append("O crítico global reprovou o rascunho depois da alteração manual.")
        for issue in review.issues[:8]:
            sync.warnings.append(f"QA {issue.severity}: {issue.description}")
    sync.generated_at = now_iso()
    write_json(paths.analysis / "sincronismo_profissional.json", sync.model_dump(mode="json"))
    return sync


def apply_manual_event_override(
    paths: ProjectPaths,
    sync: ProfessionalSyncPlan,
    catalog: dict[str, Any],
    beat_id: str,
    event_id: str,
) -> ProfessionalSyncPlan:
    evidence = {item["event_id"]: item for item in flatten_evidence(catalog)}
    item = evidence.get(event_id)
    if not item:
        raise ProfessionalSyncError("Evento de evidencia inexistente")
    target = next((beat for beat in sync.beats if beat.beat_id == beat_id), None)
    if not target:
        raise ProfessionalSyncError("Batida de sincronismo inexistente")
    wanted = target.output_end_seconds - target.output_start_seconds
    source_start, source_end = _fit_source_window(item, wanted, paths.footage)
    if source_end - source_start + 0.08 < wanted:
        raise ProfessionalSyncError("A tomada escolhida e curta demais para esta batida")
    target.event_id = event_id
    target.filename = item["filename"]
    target.source_start_seconds = source_start
    target.source_end_seconds = source_end
    target.evidence_description = item.get("description", "")
    target.match_reason = "Tomada escolhida manualmente pelo editor"
    target.match_confidence = 1.0
    target.verification = "manual"
    target.vertical_strategy = item.get("vertical_strategy", "crop")
    target.focus_x = float(item.get("focus_x", 0.5))
    target.focus_y = float(item.get("focus_y", 0.5))
    target.audio_strategy = "feature" if item.get("original_audio_is_important") else "ambient"
    target.shot_style = item.get("shot_style", "normal")
    target.sequence_index = _sequence_index(item)
    sync.warnings = [w for w in sync.warnings if target.text not in w]
    sync.rough_cut_qa = None
    # A human override is authoritative for that beat, but the assembled timeline
    # changed. Block final render until the global rough-cut critic rechecks it.
    sync.ready = False
    sync.warnings.append("Tomada alterada manualmente; revisão global do corte pendente.")
    sync.generated_at = now_iso()
    write_json(paths.analysis / "sincronismo_profissional.json", sync.model_dump(mode="json"))
    return sync
