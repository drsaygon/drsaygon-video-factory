from __future__ import annotations

import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

from app.config import (
    DIRECTED_SEARCH_MIN_CONFIDENCE,
    DIRECTED_SEARCH_OVERLAP_SECONDS,
    DIRECTED_SEARCH_SLOW_FACTOR,
    DIRECTED_SEARCH_WINDOW_SECONDS,
)
from app.models import (
    DirectedEvidenceCandidate,
    DirectedEvidenceSearchResult,
    ManualEvidenceRequest,
)
from app.services.ffmpeg import (
    apply_snap_to_cut,
    create_slow_search_proxy,
    extract_grayscale_frames,
    extract_physical_media_map,
    probe_media,
)
from app.services.gemini import find_directed_evidence
from app.services.project import ProjectPaths, now_iso, write_json


BANDING_TERMS = (
    "faixa", "banding", "flicker", "rolling shutter", "linha escura", "faixa escura",
    "faixa preta", "pisca", "oscilacao", "oscilação", "cintilacao", "cintilação",
)


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()


def _tokens(text: str) -> set[str]:
    stop = {"uma", "uns", "das", "dos", "com", "para", "quando", "onde", "isso", "esse", "essa", "que", "por"}
    return {x for x in re.findall(r"[a-z0-9]+", _plain(text)) if len(x) > 2 and x not in stop}


def _is_banding_target(target: str) -> bool:
    value = _plain(target)
    return any(_plain(term) in value for term in BANDING_TERMS)


def _video_rank(target: str, video: dict[str, Any]) -> float:
    query = _tokens(target)
    hay = [video.get("filename", ""), video.get("summary", "")]
    for segment in video.get("segments", []):
        hay.extend([
            segment.get("description", ""), segment.get("recommended_use", ""),
            " ".join(segment.get("actions", [])), " ".join(segment.get("objects", [])),
            " ".join(segment.get("states", [])), " ".join(segment.get("visual_phenomena", [])),
            " ".join(segment.get("tags", [])),
        ])
    tokens = _tokens(" ".join(hay))
    overlap = len(query & tokens) / max(1, len(query))
    filename = _plain(video.get("filename", ""))
    bonus = 0.0
    if _is_banding_target(target):
        if any(term in filename for term in ("lamp", "luz", "close", "comuta")):
            bonus += 0.35
        if any(seg.get("shot_style") in {"hold", "demo"} for seg in video.get("segments", [])):
            bonus += 0.12
    return overlap + bonus


def _window_candidates(video: dict[str, Any], duration: float, target: str) -> list[tuple[float, float, str]]:
    windows: list[tuple[float, float, str]] = []
    target_banding = _is_banding_target(target)
    for segment in video.get("segments", []):
        if not segment.get("use", True):
            continue
        start = max(0.0, float(segment.get("start_seconds", 0)))
        end = min(duration, float(segment.get("end_seconds", duration)))
        if end <= start:
            continue
        seg_text = " ".join([
            segment.get("description", ""), " ".join(segment.get("actions", [])),
            " ".join(segment.get("visual_phenomena", [])), " ".join(segment.get("objects", [])),
        ])
        lexical = len(_tokens(target) & _tokens(seg_text))
        favorable = lexical > 0 or segment.get("critical_action") or segment.get("shot_style") in {"hold", "demo"}
        if target_banding and segment.get("shot_style") in {"hold", "demo"}:
            favorable = True
        if not favorable:
            continue
        window = max(3.5, DIRECTED_SEARCH_WINDOW_SECONDS)
        if end - start <= window + 0.5:
            windows.append((start, end, f"segmento {segment.get('event_id', '')}"))
        else:
            cursor = start
            step = max(1.0, window - DIRECTED_SEARCH_OVERLAP_SECONDS)
            while cursor < end:
                stop = min(end, cursor + window)
                windows.append((cursor, stop, f"segmento {segment.get('event_id', '')}"))
                if stop >= end:
                    break
                cursor += step

    if not windows:
        window = max(4.0, DIRECTED_SEARCH_WINDOW_SECONDS)
        step = max(1.0, window - DIRECTED_SEARCH_OVERLAP_SECONDS)
        cursor = 0.0
        while cursor < duration:
            end = min(duration, cursor + window)
            windows.append((cursor, end, "varredura global"))
            if end >= duration:
                break
            cursor += step

    # Deduplicate near-identical intervals.
    dedup: list[tuple[float, float, str]] = []
    for start, end, reason in sorted(windows, key=lambda x: (x[0], x[1])):
        if dedup and abs(start - dedup[-1][0]) < 0.25 and abs(end - dedup[-1][1]) < 0.25:
            continue
        dedup.append((start, end, reason))
    return dedup


def _row_profile(frame: memoryview, width: int, height: int) -> tuple[list[float], float]:
    rows: list[float] = []
    total = 0.0
    for y in range(height):
        base = y * width
        value = float(sum(frame[base:base + width])) / float(width)
        rows.append(value)
        total += value
    global_mean = total / float(height)
    return rows, global_mean


def _column_profile(frame: memoryview, width: int, height: int) -> list[float]:
    cols = [0.0] * width
    for y in range(height):
        base = y * width
        for x in range(width):
            cols[x] += float(frame[base + x])
    return [value / float(height) for value in cols]


def _localized_profile_score(baseline: list[float], current: list[float]) -> float:
    if not baseline or len(baseline) != len(current):
        return 0.0
    residual = [max(0.0, baseline[i] - current[i]) for i in range(len(current))]
    best = 0.0
    for window_size in (3, 5, 8, 12):
        if window_size >= len(current):
            continue
        running = sum(residual[:window_size])
        best = max(best, running / window_size)
        for i in range(window_size, len(current)):
            running += residual[i] - residual[i - window_size]
            best = max(best, running / window_size)
    broad = sum(abs(baseline[i] - current[i]) for i in range(len(current))) / max(1, len(current))
    localized = max(0.0, best - broad * 0.22)
    return max(0.0, min(1.0, localized / 0.16))


def _banding_hotspots(source: Path, start: float, end: float, limit: int = 8) -> list[tuple[float, float, str]]:
    """Cheap CPU-only detector for moving horizontal luminance bands.

    It never declares ground truth. It only proposes timestamps for the adversarial
    Gemini verifier. The score emphasizes a *localized* row-level darkening after
    normalizing global brightness, which helps distinguish a moving dark band from
    the whole scene simply getting dimmer.
    """
    raw, width, height, fps = extract_grayscale_frames(source, start, end, width=96, height=54, fps=24)
    frame_size = width * height
    if frame_size <= 0 or len(raw) < frame_size * 4:
        return []
    frame_count = len(raw) // frame_size
    baseline_rows: list[float] | None = None
    baseline_cols: list[float] | None = None
    scores: list[tuple[float, float, str]] = []
    view = memoryview(raw)
    for idx in range(frame_count):
        frame = view[idx * frame_size:(idx + 1) * frame_size]
        rows, global_mean = _row_profile(frame, width, height)
        if global_mean < 8.0:
            continue
        cols = _column_profile(frame, width, height)
        normalized_rows = [value / global_mean for value in rows]
        normalized_cols = [value / global_mean for value in cols]
        if baseline_rows is None or baseline_cols is None:
            baseline_rows = normalized_rows[:]
            baseline_cols = normalized_cols[:]
            continue
        row_score = _localized_profile_score(baseline_rows, normalized_rows)
        col_score = _localized_profile_score(baseline_cols, normalized_cols)
        score = max(row_score, col_score)
        orientation = "horizontal" if row_score >= col_score else "vertical"
        timestamp = start + idx / float(fps)
        if score >= 0.24:
            scores.append((timestamp, score, orientation))
        # Slow baseline learning; freeze almost completely on anomalies.
        alpha = 0.006 if score > 0.35 else 0.035
        baseline_rows = [baseline_rows[y] * (1.0 - alpha) + normalized_rows[y] * alpha for y in range(height)]
        baseline_cols = [baseline_cols[x] * (1.0 - alpha) + normalized_cols[x] * alpha for x in range(width)]

    if not scores:
        return []
    scores.sort(key=lambda x: x[1], reverse=True)
    chosen: list[tuple[float, float, str]] = []
    for timestamp, score, orientation in scores:
        if any(abs(timestamp - existing) < 0.55 for existing, _, _ in chosen):
            continue
        chosen.append((timestamp, score, orientation))
        if len(chosen) >= limit:
            break
    return sorted(chosen, key=lambda x: x[0])


def search_directed_evidence(
    paths: ProjectPaths,
    catalog: dict[str, Any],
    target: str,
    prompt_text: str,
    logger: Callable[[str], None],
    filenames: list[str] | None = None,
    max_candidates: int = 6,
    progress: Callable[[int, str], None] | None = None,
) -> DirectedEvidenceSearchResult:
    progress_cb = progress or (lambda _pct, _msg: None)
    progress_cb(1, "Classificando filmagens candidatas")
    videos = catalog.get("videos", [])
    allowed = set(filenames or [])
    if allowed:
        videos = [video for video in videos if video.get("filename") in allowed]
    videos = sorted(videos, key=lambda v: (-_video_rank(target, v), int(v.get("sequence_index", 0))))
    videos = videos[: max(1, min(3, len(videos)))]

    temp = paths.temp / "directed_evidence"
    temp.mkdir(parents=True, exist_ok=True)
    candidate_windows: list[tuple[dict[str, Any], float, float, str, float]] = []
    banding = _is_banding_target(target)

    for video_index, video in enumerate(videos, start=1):
        filename = video.get("filename", "")
        progress_cb(3 + int((video_index - 1) / max(1, len(videos)) * 15), f"Varredura local {video_index}/{len(videos)}: {filename}")
        source = paths.footage / filename
        if not source.exists():
            continue
        duration = probe_media(source).duration
        windows = _window_candidates(video, duration, target)
        local_hotspots: list[tuple[float, float, str]] = []
        if banding:
            try:
                for start, end, _ in windows[:12]:
                    local_hotspots.extend(_banding_hotspots(source, start, end, limit=5))
            except Exception as exc:
                logger(f"Detector local de banding nao conseguiu analisar {filename}: {exc}")
        if local_hotspots:
            for timestamp, score, orientation in sorted(local_hotspots, key=lambda x: x[1], reverse=True)[:6]:
                start = max(0.0, timestamp - 1.4)
                end = min(duration, timestamp + 1.8)
                candidate_windows.append((video, start, end, f"hotspot local de faixa/flicker {orientation}", score))
        # Always include a few semantic/static windows as a safety net.
        for start, end, reason in windows[:5]:
            candidate_windows.append((video, start, end, reason, 0.0))

    # Prefer local hotspots, then earlier sequence/timeline. Deduplicate overlapping windows.
    candidate_windows.sort(key=lambda x: (-x[4], int(x[0].get("sequence_index", 0)), x[1]))
    dedup: list[tuple[dict[str, Any], float, float, str, float]] = []
    # Autopilot rescue is not a forensic exhaustive scan. The broad catalog
    # already narrowed the search; keep the expensive multimodal budget tight.
    # Manual Evidence Lab can still request a broader search explicitly.
    window_budget = 8 if banding else 6
    if allowed:
        window_budget = min(window_budget, 6)
    for item in candidate_windows:
        video, start, end, reason, local_score = item
        if any(
            v.get("filename") == video.get("filename") and max(0.0, min(end, e) - max(start, s)) > 0.55 * min(end - start, e - s)
            for v, s, e, _, _ in dedup
        ):
            continue
        dedup.append(item)
        if len(dedup) >= window_budget:
            break

    results: list[DirectedEvidenceCandidate] = []
    warnings: list[str] = []
    progress_cb(20, f"Verificando {len(dedup)} microtrecho(s) prioritário(s)")
    for idx, (video, start, end, reason, local_score) in enumerate(dedup, start=1):
        filename = video.get("filename", "")
        source = paths.footage / filename
        slow_path = temp / f"search_{idx:03d}.mp4"
        pct = 20 + int((idx - 1) / max(1, len(dedup)) * 72)
        progress_cb(pct, f"Verificando microtrecho {idx}/{len(dedup)}: {filename}")
        logger(f"Busca dirigida {idx}/{len(dedup)}: {filename} [{start:.2f}-{end:.2f}] ({reason})")
        create_slow_search_proxy(source, slow_path, start, end, DIRECTED_SEARCH_SLOW_FACTOR)
        try:
            finding, model = find_directed_evidence(
                slow_path,
                target,
                f"Arquivo {filename}; janela original {start:.3f}-{end:.3f}s; vídeo foi desacelerado {DIRECTED_SEARCH_SLOW_FACTOR:.1f}x.",
                prompt_text,
                logger,
            )
        except Exception as exc:
            warnings.append(f"Falha ao verificar {filename} {start:.1f}-{end:.1f}s: {exc}")
            continue
        if not finding.confirmed:
            continue
        relative_start = finding.start_seconds if finding.start_seconds is not None else 0.0
        relative_end = finding.end_seconds if finding.end_seconds is not None else (end - start) * DIRECTED_SEARCH_SLOW_FACTOR
        mapped_start = start + max(0.0, float(relative_start)) / DIRECTED_SEARCH_SLOW_FACTOR
        mapped_end = start + max(float(relative_start) + 0.2, float(relative_end)) / DIRECTED_SEARCH_SLOW_FACTOR
        try:
            phys_map = extract_physical_media_map(source)
            safe_cuts = phys_map.get("safe_cut_timestamps", [])
            if safe_cuts:
                mapped_start = apply_snap_to_cut(mapped_start, safe_cuts, threshold=0.40)
                mapped_end = apply_snap_to_cut(mapped_end, safe_cuts, threshold=0.40)
        except Exception:
            pass
        mapped_start = max(start, min(mapped_start, end))
        mapped_end = max(mapped_start + 0.2, min(mapped_end, end))
        combined_confidence = max(float(finding.confidence), min(0.88, 0.45 + local_score * 0.4)) if local_score else float(finding.confidence)
        if combined_confidence < DIRECTED_SEARCH_MIN_CONFIDENCE:
            continue
        results.append(DirectedEvidenceCandidate(
            filename=filename,
            source_start_seconds=round(mapped_start, 3),
            source_end_seconds=round(mapped_end, 3),
            observed_evidence=finding.observed_evidence,
            confidence=round(min(1.0, combined_confidence), 3),
            confirmed=True,
            model=model,
            detection_source="local_banding" if local_score else "gemini_slow_scan",
            visual_phenomena=finding.visual_phenomena,
            actions=finding.actions,
            states=finding.states,
        ))
        progress_cb(min(94, pct + 4), f"Evidência confirmada: {filename} · {combined_confidence:.0%}")
        # Precision-first stop: for a directed visual phenomenon, two strong
        # independently verified candidates are enough to let the author choose.
        # This avoids burning free-tier calls merely to fill six slots.
        strong = [c for c in results if c.confidence >= 0.85]
        strong_files = {c.filename for c in strong}
        if banding and len(strong) >= 2 and len(strong_files) >= 1:
            logger(f"Busca dirigida encerrou cedo: {len(strong)} candidatos fortes já confirmados.")
            break
        if len(results) >= max_candidates:
            break

    progress_cb(97, "Consolidando candidatos verificados")
    result = DirectedEvidenceSearchResult(
        target=target,
        created_at=now_iso(),
        candidates=sorted(results, key=lambda c: c.confidence, reverse=True),
        warnings=warnings,
    )
    write_json(paths.analysis / "busca_dirigida_evidencias.json", result.model_dump(mode="json"))
    return result


def add_manual_evidence(
    paths: ProjectPaths,
    catalog: dict[str, Any],
    payload: ManualEvidenceRequest,
) -> dict[str, Any]:
    video = next((v for v in catalog.get("videos", []) if v.get("filename") == payload.filename), None)
    if not video:
        raise ValueError(f"Filmagem nao encontrada no catalogo: {payload.filename}")
    source = paths.footage / payload.filename
    info = probe_media(source)
    start = max(0.0, min(payload.start_seconds, info.duration))
    end = max(start + 0.2, min(payload.end_seconds, info.duration))
    existing = video.get("segments", [])
    manual_number = 1 + sum(1 for item in existing if str(item.get("event_id", "")).startswith("user_"))
    sequence_index = int(video.get("sequence_index", 0))
    segment = {
        "start_seconds": round(start, 3),
        "end_seconds": round(end, 3),
        "description": payload.label,
        "quality": "excelente",
        "tags": ["evidencia confirmada pelo usuario"],
        "recommended_use": "Evidência confirmada manualmente pelo autor do material.",
        "use": True,
        "original_audio_is_important": payload.event_kind == "speech",
        "focus_x": 0.5,
        "focus_y": 0.5,
        "vertical_strategy": "crop",
        "shot_style": "demo" if end - start >= 2.0 else "quick",
        "min_take_seconds": min(1.5, max(0.3, end - start)),
        "max_take_seconds": max(0.5, end - start),
        "event_id": f"user_s{sequence_index:03d}_{manual_number:03d}",
        "event_kind": payload.event_kind,
        "actions": payload.actions,
        "objects": [],
        "states": payload.states,
        "visual_phenomena": payload.visual_phenomena or ([payload.label] if payload.event_kind == "phenomenon" else []),
        "evidence_timestamps": [round((start + end) / 2.0, 3)],
        "confidence": 1.0,
        "critical_action": payload.critical_action,
        "verified_by_user": True,
        "detection_source": "manual",
    }
    existing.append(segment)
    video["segments"] = sorted(existing, key=lambda item: (float(item.get("start_seconds", 0)), str(item.get("event_id", ""))))
    write_json(paths.analysis / "catalogo_cenas.json", catalog)
    return segment
