from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any

from app.config import MAX_STATIC_SCENE_SECONDS
from app.models import EditQualityReport, EditingStyle, ProfessionalSyncBeat, VisualRun
from app.services.ffmpeg import probe_media


REPLAY_TERMS = (
    "de novo", "novamente", "replay", "repet", "como eu mostrei", "voltando", "volta aqui",
)


def _plain(value: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def _allows_replay(text: str) -> bool:
    value = _plain(text)
    return any(term in value for term in REPLAY_TERMS)


def infer_editing_style(
    requested: EditingStyle,
    beats: list[ProfessionalSyncBeat],
    evidence_by_id: dict[str, dict[str, Any]],
) -> EditingStyle:
    if requested != "auto":
        return requested
    if not beats:
        return "balanced"
    sustained = 0.0
    total = 0.0
    for beat in beats:
        total += beat.duration
        item = evidence_by_id.get(beat.event_id, {})
        style = item.get("shot_style") or beat.shot_style
        if style in {"hold", "demo"}:
            sustained += beat.duration
    ratio = sustained / max(0.01, total)
    if ratio >= 0.48:
        return "demonstrative"
    if ratio <= 0.16:
        return "dynamic"
    return "balanced"


def _dominant_anchor(
    block: list[ProfessionalSyncBeat],
    evidence_by_id: dict[str, dict[str, Any]],
) -> tuple[str, str] | None:
    """Find a verified/manual event that deserves one long take.

    The important distinction is verification != edit point. Several semantic beats
    can prove the same phenomenon and should still be one continuous shot.
    """
    if not block:
        return None
    duration = sum(b.duration for b in block)
    weighted: dict[tuple[str, str], float] = defaultdict(float)
    counts: Counter[tuple[str, str]] = Counter()
    for beat in block:
        if not beat.filename or beat.verification == "failed":
            continue
        item = evidence_by_id.get(beat.event_id, {})
        style = item.get("shot_style") or beat.shot_style
        verified = beat.verification in {"verified", "manual"} or bool(item.get("verified_by_user"))
        phenomenon = bool(item.get("visual_phenomena")) or item.get("event_kind") in {"phenomenon", "demo"}
        if verified or phenomenon or style in {"hold", "demo"}:
            key = (beat.filename, beat.event_id)
            weight = beat.duration * (1.55 if verified else 1.0)
            if phenomenon:
                weight *= 1.20
            weighted[key] += weight
            counts[key] += 1
    if not weighted:
        return None
    key = max(weighted, key=weighted.get)
    raw_duration = sum(b.duration for b in block if (b.filename, b.event_id) == key)
    same_file_duration = sum(b.duration for b in block if b.filename == key[0])
    if counts[key] >= 2 and (raw_duration >= duration * 0.25 or same_file_duration >= duration * 0.52):
        return key
    if weighted[key] >= duration * 0.58:
        return key
    return None


def _has_conflicting_action(beat: ProfessionalSyncBeat, anchor_item: dict[str, Any]) -> bool:
    """Do not swallow a concrete action into a generic phenomenon hold."""
    if not beat.critical:
        return False
    text = _plain(beat.required_observation or beat.text)
    action_terms = (
        "tomada", "plugue", "conect", "desconect", "retiro", "retir", "tiro", "ligo", "liga",
        "desliga", "pression", "aperto", "botao", "interruptor", "abre", "fecha", "coloco", "remove",
    )
    anchor_text = _plain(" ".join(
        [anchor_item.get("description", "")] + anchor_item.get("actions", []) + anchor_item.get("visual_phenomena", [])
    ))
    return any(term in text and term not in anchor_text for term in action_terms)


def _rebind_block_to_anchor(
    block: list[ProfessionalSyncBeat],
    anchor: tuple[str, str] | None,
    evidence_by_id: dict[str, dict[str, Any]],
    repairs: list[str],
) -> None:
    if not anchor:
        return
    filename, event_id = anchor
    anchor_item = evidence_by_id.get(event_id)
    if not anchor_item:
        return
    changed: list[str] = []
    for beat in block:
        if beat.verification == "failed" or _allows_replay(beat.text):
            continue
        if beat.filename != filename:
            # Stay conservative: only absorb a different source when the beat is
            # non-critical and generic, and both neighbors already live on the anchor.
            continue
        if beat.event_id == event_id:
            continue
        if _has_conflicting_action(beat, anchor_item):
            continue
        # If the same physical take is already semantically acceptable, keep the
        # verified anchor instead of jumping to another timestamp in the same file.
        beat.event_id = event_id
        beat.evidence_description = anchor_item.get("description", beat.evidence_description)
        beat.match_reason = f"{beat.match_reason} | Continuity Engine: ancora visual dominante".strip(" |")
        beat.match_confidence = max(beat.match_confidence, min(1.0, float(anchor_item.get("confidence", 0.8))))
        if beat.verification == "semantic" and anchor_item.get("verified_by_user"):
            beat.verification = "manual"
        beat.vertical_strategy = anchor_item.get("vertical_strategy", beat.vertical_strategy)
        beat.focus_x = float(anchor_item.get("focus_x", beat.focus_x))
        beat.focus_y = float(anchor_item.get("focus_y", beat.focus_y))
        beat.shot_style = anchor_item.get("shot_style", beat.shot_style)
        changed.append(beat.beat_id)
    # Remove a one-beat B-roll detour sandwiched between the same sustained
    # anchor. This is a generic human-editing rule: a new sentence is not enough
    # reason to cut away and immediately cut back.
    for idx in range(1, len(block) - 1):
        beat = block[idx]
        if beat.event_id == event_id or beat.critical or beat.verification != "semantic" or _allows_replay(beat.text):
            continue
        if block[idx - 1].event_id != event_id or block[idx + 1].event_id != event_id:
            continue
        beat.event_id = event_id
        beat.filename = filename
        beat.evidence_description = anchor_item.get("description", beat.evidence_description)
        beat.match_reason = f"{beat.match_reason} | Continuity Engine: removido desvio B-roll entre a mesma tomada".strip(" |")
        beat.match_confidence = max(beat.match_confidence, min(1.0, float(anchor_item.get("confidence", 0.8))))
        if anchor_item.get("verified_by_user"):
            beat.verification = "manual"
        beat.vertical_strategy = anchor_item.get("vertical_strategy", beat.vertical_strategy)
        beat.focus_x = float(anchor_item.get("focus_x", beat.focus_x))
        beat.focus_y = float(anchor_item.get("focus_y", beat.focus_y))
        beat.shot_style = anchor_item.get("shot_style", beat.shot_style)
        beat.sequence_index = int(anchor_item.get("sequence_index", beat.sequence_index) or 0)
        changed.append(beat.beat_id)

    if changed:
        repairs.append(
            f"Bloco {block[0].block_number}: {len(set(changed))} batida(s) mantidas na tomada dominante "
            f"{filename} para evitar cortes/replays artificiais."
        )


def _can_merge(a: ProfessionalSyncBeat, b: ProfessionalSyncBeat) -> bool:
    if a.block_number != b.block_number or not a.filename or a.filename != b.filename:
        return False
    if a.verification == "failed" or b.verification == "failed":
        return False
    if _allows_replay(b.text):
        return False
    if a.event_id == b.event_id:
        return True
    # Same take and heavily overlapping/adjacent source windows: an editor normally
    # holds the shot instead of cutting out and restarting it for a new sentence.
    overlap = min(a.source_end_seconds, b.source_end_seconds) - max(a.source_start_seconds, b.source_start_seconds)
    near = abs(b.source_start_seconds - a.source_end_seconds) <= 0.75
    return overlap > 0.20 or near


def _fit_event_for_run(
    item: dict[str, Any], duration: float, footage: Path
) -> tuple[float, float]:
    source = footage / item["filename"]
    info = probe_media(source)
    seg_start = max(0.0, min(float(item.get("start_seconds", 0)), info.duration))
    seg_end = max(seg_start + 0.05, min(float(item.get("end_seconds", info.duration)), info.duration))
    points = [
        float(x) for x in item.get("evidence_timestamps", [])
        if seg_start <= float(x) <= seg_end
    ]
    center = points[len(points) // 2] if points else (seg_start + seg_end) / 2.0
    wanted = max(0.35, float(duration))
    if seg_end - seg_start >= wanted:
        # For a long demonstration prefer natural forward progression starting
        # before the event centre, not a tiny replay centred for every phrase.
        start = max(seg_start, min(center - wanted * 0.42, seg_end - wanted))
        return round(start, 3), round(start + wanted, 3)
    start = max(0.0, center - wanted * 0.42)
    end = min(info.duration, start + wanted)
    start = max(0.0, end - wanted)
    return round(start, 3), round(end, 3)


def _verification_for(beats: list[ProfessionalSyncBeat]) -> str:
    values = {b.verification for b in beats}
    if "manual" in values:
        return "manual"
    if "verified" in values:
        return "verified"
    if "failed" in values:
        return "failed"
    return "semantic"


def _make_run(
    run_number: int,
    group: list[ProfessionalSyncBeat],
    evidence_by_id: dict[str, dict[str, Any]],
    footage: Path,
) -> VisualRun:
    first, last = group[0], group[-1]
    duration = last.output_end_seconds - first.output_start_seconds
    event_counts = Counter(b.event_id for b in group if b.event_id)
    event_id = event_counts.most_common(1)[0][0] if event_counts else first.event_id
    item = evidence_by_id.get(event_id)
    if item and item.get("filename") == first.filename:
        source_start, source_end = _fit_event_for_run(item, duration, footage)
    else:
        # Use the union and then expand only as required. This path covers a real
        # continuous take whose semantic evidence IDs differ.
        info = probe_media(footage / first.filename)
        union_start = min(b.source_start_seconds for b in group)
        union_end = max(b.source_end_seconds for b in group)
        center = (union_start + union_end) / 2.0
        source_start = max(0.0, min(center - duration / 2.0, max(0.0, info.duration - duration)))
        source_end = min(info.duration, source_start + duration)
        source_start = max(0.0, source_end - duration)
    # Project the continuous source timeline back onto the semantic beats. Their
    # metadata/previews remain useful, but they no longer imply a cut.
    cursor = source_start
    for beat in group:
        beat.source_start_seconds = round(cursor, 3)
        cursor += beat.duration
        beat.source_end_seconds = round(cursor, 3)
    confidence = min((b.match_confidence for b in group), default=0.0)
    descriptions = [b.evidence_description for b in group if b.evidence_description]
    return VisualRun(
        run_id=f"r{run_number:03d}",
        block_number=first.block_number,
        output_start_seconds=round(first.output_start_seconds, 3),
        output_end_seconds=round(last.output_end_seconds, 3),
        filename=first.filename,
        source_start_seconds=round(source_start, 3),
        source_end_seconds=round(source_end, 3),
        beat_ids=[b.beat_id for b in group],
        event_ids=list(dict.fromkeys(b.event_id for b in group if b.event_id)),
        evidence_description=descriptions[0] if descriptions else "",
        reason=(
            "Continuidade editorial: uma tomada longa cobre várias batidas semânticas."
            if len(group) > 1 else "Corte necessário por mudança real de ação/estado/tomada."
        ),
        verification=_verification_for(group),
        confidence=round(confidence, 3),
        vertical_strategy=first.vertical_strategy,
        focus_x=first.focus_x,
        focus_y=first.focus_y,
        audio_strategy=first.audio_strategy,
        shot_style="hold" if len(group) > 1 else first.shot_style,
        sequence_index=first.sequence_index,
        allows_replay=any(_allows_replay(b.text) for b in group),
        visual_anchor=first.visual_anchor,
        phonetic_budget=first.phonetic_budget,
        sync_precision_score=round(sum(getattr(b, "sync_precision_score", 100.0) for b in group) / max(1, len(group)), 2),
        sync_drift_ms=round(sum(getattr(b, "sync_drift_ms", 0.0) for b in group) / max(1, len(group)), 2),
        applied_sync_strategy=getattr(first, "applied_sync_strategy", "native") or "native",
        visual_complexity=getattr(first, "visual_complexity", "medium") or "medium",
        retention_hook_type=getattr(first, "retention_hook_type", "standard") or "standard",
    )


def _stitch_adjacent_runs(
    runs: list[VisualRun],
    evidence_by_id: dict[str, dict[str, Any]],
    footage: Path,
    repairs: list[str],
) -> None:
    """Keep source time moving forward even across narration-block boundaries."""
    i = 0
    while i < len(runs):
        j = i + 1
        shared_events = set(runs[i].event_ids)
        while j < len(runs):
            if runs[j].filename != runs[i].filename:
                break
            intersection = shared_events & set(runs[j].event_ids)
            if not intersection:
                break
            shared_events = intersection
            j += 1
        if j - i >= 2 and shared_events:
            group = runs[i:j]
            event_id = next(iter(shared_events))
            item = evidence_by_id.get(event_id)
            if item:
                total = sum(r.duration for r in group)
                evidence_span = max(0.0, float(item.get("end_seconds", 0)) - float(item.get("start_seconds", 0)))
                # Only stitch across blocks when the verified passage itself is
                # long enough. Otherwise keep one long run per block rather than
                # destroying event timing to force an impossible continuous take.
                if evidence_span + 0.35 < total * 0.90:
                    i = max(j, i + 1)
                    continue
                start, end = _fit_event_for_run(item, total, footage)
                if end - start + 0.08 >= total:
                    cursor = start
                    for run in group:
                        run.source_start_seconds = round(cursor, 3)
                        cursor += run.duration
                        run.source_end_seconds = round(cursor, 3)
                        run.reason += " Continuidade de fonte preservada entre blocos."
                    repairs.append(
                        f"{len(group)} tomadas adjacentes em {group[0].filename} foram costuradas "
                        "numa progressao temporal continua entre blocos."
                    )
        i = max(j, i + 1)



def _beat_map(beats: list[ProfessionalSyncBeat]) -> dict[str, ProfessionalSyncBeat]:
    return {beat.beat_id: beat for beat in beats}


def _run_beats(run: VisualRun, beats_by_id: dict[str, ProfessionalSyncBeat]) -> list[ProfessionalSyncBeat]:
    return [beats_by_id[beat_id] for beat_id in run.beat_ids if beat_id in beats_by_id]


def _best_run_anchor(
    runs: list[VisualRun],
    evidence_by_id: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]] | None:
    """Pick the safest long-take anchor already present in a structural neighborhood."""
    best: tuple[float, str, dict[str, Any]] | None = None
    for run in runs:
        for event_id in run.event_ids:
            item = evidence_by_id.get(event_id)
            if not item or item.get("filename") != run.filename:
                continue
            span = max(0.0, float(item.get("end_seconds", 0)) - float(item.get("start_seconds", 0)))
            score = float(item.get("confidence", 0.0)) * 10.0 + min(30.0, span)
            if item.get("verified_by_user"):
                score += 120.0
            if item.get("event_kind") in {"phenomenon", "demo"} or item.get("visual_phenomena"):
                score += 35.0
            if item.get("shot_style") in {"hold", "demo"}:
                score += 20.0
            if run.verification in {"manual", "verified"}:
                score += 18.0
            candidate = (score, event_id, item)
            if best is None or candidate[0] > best[0]:
                best = candidate
    return (best[1], best[2]) if best else None


def _bind_beat_to_anchor(
    beat: ProfessionalSyncBeat,
    event_id: str,
    item: dict[str, Any],
    reason: str,
) -> None:
    beat.event_id = event_id
    beat.filename = str(item.get("filename", beat.filename))
    beat.evidence_description = item.get("description", beat.evidence_description)
    beat.match_reason = f"{beat.match_reason} | {reason}".strip(" |")
    beat.match_confidence = max(beat.match_confidence, min(1.0, float(item.get("confidence", 0.80))))
    if item.get("verified_by_user"):
        beat.verification = "manual"
    beat.vertical_strategy = item.get("vertical_strategy", beat.vertical_strategy)
    beat.focus_x = float(item.get("focus_x", beat.focus_x))
    beat.focus_y = float(item.get("focus_y", beat.focus_y))
    beat.shot_style = item.get("shot_style", beat.shot_style)
    beat.sequence_index = int(item.get("sequence_index", beat.sequence_index) or 0)


def _repair_ping_pong_detours(
    beats: list[ProfessionalSyncBeat],
    runs: list[VisualRun],
    evidence_by_id: dict[str, dict[str, Any]],
    repairs: list[str],
) -> bool:
    """Repair A-B-A automatically when B is only explanatory/contextual B-roll.

    In Autopilot a contextual sentence is never a sufficient reason to leave a
    strong demonstration shot and immediately return to it.
    """
    beats_by_id = _beat_map(beats)
    changed = False
    for left, middle, right in zip(runs, runs[1:], runs[2:]):
        if left.filename != right.filename or left.filename == middle.filename:
            continue
        middle_beats = _run_beats(middle, beats_by_id)
        if not middle_beats or any(b.visual_obligation == "hard" for b in middle_beats):
            continue
        anchor = _best_run_anchor([left, right], evidence_by_id)
        if not anchor:
            continue
        event_id, item = anchor
        for beat in middle_beats:
            if beat.verification == "failed" or _has_conflicting_action(beat, item):
                continue
            _bind_beat_to_anchor(
                beat, event_id, item,
                "Continuity AutoRepair: removido desvio A-B-A sem necessidade editorial",
            )
            changed = True
        if changed:
            repairs.append(
                f"Autocorreção A-B-A: B-roll intermediário removido; continuidade mantida em {left.filename}."
            )
            break
    return changed


def _continuous_lane_start(
    group: list[VisualRun],
    beats_by_id: dict[str, ProfessionalSyncBeat],
    evidence_by_id: dict[str, dict[str, Any]],
    footage: Path,
) -> float | None:
    """Find a 1x continuous source lane that still intersects every HARD proof.

    This is the key anti-replay repair. Instead of replaying the same 2 seconds
    for several consecutive phrases, the source clock keeps moving forward while
    guaranteeing that literal actions/phenomena remain visible during their beats.
    """
    if not group or len({run.filename for run in group}) != 1:
        return None
    filename = group[0].filename
    if not filename:
        return None
    info = probe_media(footage / filename)
    group_start = group[0].output_start_seconds
    group_end = group[-1].output_end_seconds
    total = max(0.0, group_end - group_start)
    if total <= 0.05 or total > info.duration + 0.05:
        return None

    low = 0.0
    high = max(0.0, info.duration - total)
    preferred: list[float] = []
    hard_constraints = 0
    for run in group:
        for beat in _run_beats(run, beats_by_id):
            if beat.visual_obligation != "hard":
                continue
            item = evidence_by_id.get(beat.event_id)
            if not item or item.get("filename") != filename:
                continue
            es = max(0.0, min(float(item.get("start_seconds", 0)), info.duration))
            ee = max(es + 0.01, min(float(item.get("end_seconds", es + 0.01)), info.duration))
            beat_a = max(0.0, beat.output_start_seconds - group_start)
            beat_b = max(beat_a + 0.01, beat.output_end_seconds - group_start)
            # Require at least an intersection between the continuously playing
            # source during this spoken beat and the verified evidence interval.
            low = max(low, es - beat_b)
            high = min(high, ee - beat_a)
            points = [float(x) for x in item.get("evidence_timestamps", []) if es <= float(x) <= ee]
            center = points[len(points) // 2] if points else (es + ee) / 2.0
            preferred.append(center - (beat_a + beat_b) / 2.0)
            hard_constraints += 1

    if low > high + 1e-6:
        return None
    if preferred:
        candidate = float(median(preferred))
    else:
        candidate = float(group[0].source_start_seconds)
    return round(max(low, min(high, candidate)), 3)


def _stitch_same_file_lanes(
    runs: list[VisualRun],
    beats: list[ProfessionalSyncBeat],
    evidence_by_id: dict[str, dict[str, Any]],
    footage: Path,
    repairs: list[str],
) -> None:
    """Eliminate same-file replay even when evidence IDs differ across blocks."""
    beats_by_id = _beat_map(beats)
    i = 0
    while i < len(runs):
        j = i + 1
        while j < len(runs) and runs[j].filename == runs[i].filename and not runs[j].allows_replay:
            j += 1
        group = runs[i:j]
        if len(group) >= 2 and group[0].filename:
            start = _continuous_lane_start(group, beats_by_id, evidence_by_id, footage)
            if start is not None:
                cursor = start
                changed = False
                for run in group:
                    old = (run.source_start_seconds, run.source_end_seconds)
                    run.source_start_seconds = round(cursor, 3)
                    cursor += run.duration
                    run.source_end_seconds = round(cursor, 3)
                    if abs(old[0] - run.source_start_seconds) > 0.05 or abs(old[1] - run.source_end_seconds) > 0.05:
                        changed = True
                    run.reason += " Source Lane v6: relógio da filmagem mantido em progressão contínua."
                if changed:
                    repairs.append(
                        f"Source Lane: {len(group)} tomadas consecutivas de {group[0].filename} foram alinhadas "
                        "num único relógio de fonte, sem replay de timestamps."
                    )
        i = max(j, i + 1)


def _group_semantic_beats(beats: list[ProfessionalSyncBeat]) -> list[list[ProfessionalSyncBeat]]:
    by_block: dict[int, list[ProfessionalSyncBeat]] = defaultdict(list)
    for beat in sorted(beats, key=lambda b: (b.output_start_seconds, b.block_number, b.beat_number)):
        by_block[beat.block_number].append(beat)
    groups: list[list[ProfessionalSyncBeat]] = []
    for block_number in sorted(by_block):
        block = by_block[block_number]
        current: list[ProfessionalSyncBeat] = []
        for beat in block:
            if not current:
                current = [beat]
            elif _can_merge(current[-1], beat):
                current.append(beat)
            else:
                groups.append(current)
                current = [beat]
        if current:
            groups.append(current)
    return groups


def _render_runs_from_beats(
    beats: list[ProfessionalSyncBeat],
    evidence_by_id: dict[str, dict[str, Any]],
    footage: Path,
    repairs: list[str],
) -> list[VisualRun]:
    groups = _group_semantic_beats(beats)
    runs = [_make_run(i, group, evidence_by_id, footage) for i, group in enumerate(groups, start=1)]
    _stitch_adjacent_runs(runs, evidence_by_id, footage, repairs)
    _stitch_same_file_lanes(runs, beats, evidence_by_id, footage, repairs)
    return runs


def apply_conservative_autopilot_policy(
    beats: list[ProfessionalSyncBeat],
    evidence_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    """Fallback used only after the real rough-cut critic asks for another edit.

    It aggressively holds the strongest verified/demo shot inside each block for
    explanatory beats, while NEVER swallowing a conflicting HARD physical action.
    This is deliberately conservative: on an automatic retry, boring-but-correct
    is preferable to frantic or semantically loose B-roll.
    """
    repairs: list[str] = []
    by_block: dict[int, list[ProfessionalSyncBeat]] = defaultdict(list)
    for beat in beats:
        by_block[beat.block_number].append(beat)
    for block_number, block in sorted(by_block.items()):
        anchor = _dominant_anchor(block, evidence_by_id)
        if not anchor:
            # Fall back to the strongest hold/demo already present in the block.
            candidates: list[tuple[float, str, dict[str, Any]]] = []
            for beat in block:
                item = evidence_by_id.get(beat.event_id)
                if not item or beat.verification == "failed":
                    continue
                if item.get("shot_style") not in {"hold", "demo"} and not item.get("verified_by_user"):
                    continue
                score = float(item.get("confidence", 0.0)) + (1.0 if item.get("verified_by_user") else 0.0)
                candidates.append((score, beat.event_id, item))
            if not candidates:
                continue
            _, event_id, item = max(candidates, key=lambda x: x[0])
            anchor = (str(item.get("filename", "")), event_id)
        filename, event_id = anchor
        item = evidence_by_id.get(event_id)
        if not item:
            continue
        changed = 0
        for beat in block:
            if beat.verification == "failed" or beat.visual_obligation == "hard" or _allows_replay(beat.text):
                continue
            if _has_conflicting_action(beat, item):
                continue
            if beat.event_id == event_id and beat.filename == filename:
                continue
            _bind_beat_to_anchor(
                beat, event_id, item,
                "Autopilot Safe Cut: tomada dominante mantida após crítica do rascunho",
            )
            changed += 1
        if changed:
            repairs.append(
                f"Safe Cut bloco {block_number}: {changed} batida(s) explicativas consolidadas em {filename}."
            )
    return repairs

def build_visual_runs(
    beats: list[ProfessionalSyncBeat],
    evidence_by_id: dict[str, dict[str, Any]],
    footage: Path,
    requested_style: EditingStyle = "auto",
    *,
    long_form: bool = False,
) -> tuple[list[VisualRun], EditQualityReport]:
    """Turn evidence-rich semantic beats into actual edit decisions.

    v6 treats semantic beats as evidence, never as automatic cut points. Long-form
    defaults to sustained human pacing; deterministic defects trigger repair passes
    instead of sending the creator back to the timeline.
    """
    repairs: list[str] = []
    by_block: dict[int, list[ProfessionalSyncBeat]] = defaultdict(list)
    for beat in sorted(beats, key=lambda b: (b.output_start_seconds, b.block_number, b.beat_number)):
        by_block[beat.block_number].append(beat)

    style = infer_editing_style(requested_style, beats, evidence_by_id)
    # Long-form YouTube defaults to calm continuity. A sentence boundary is not a cut.
    if long_form and requested_style == "auto":
        style = "demonstrative"
    for block in by_block.values():
        _rebind_block_to_anchor(block, _dominant_anchor(block, evidence_by_id), evidence_by_id, repairs)

    runs = _render_runs_from_beats(beats, evidence_by_id, footage, repairs)
    quality = audit_visual_runs(runs, style, repairs, long_form=long_form)

    # Long-form anti-microcut pass. The default product promise is calm,
    # creator-style editing rather than Shorts pacing. Above ~3 cuts/minute,
    # consolidate contextual/abstract beats into strong long-take anchors.
    # HARD actions remain protected and can still trigger a precise short cut.
    if long_form and (quality.cuts_per_minute > 3.0 or quality.short_run_count >= 3):
        long_repairs = apply_conservative_autopilot_policy(beats, evidence_by_id)
        if long_repairs:
            repairs.extend(long_repairs)
            repairs.append(
                f"Long-form v6: ritmo reduzido de {quality.cuts_per_minute:.1f} cortes/min; "
                "frases explicativas foram mantidas em tomadas sustentadas."
            )
            runs = _render_runs_from_beats(beats, evidence_by_id, footage, repairs)
            quality = audit_visual_runs(runs, style, repairs, long_form=True)

    # Structural defects are not user exceptions. Repair A-B-A detours and then
    # rebuild source lanes. Up to three passes keeps the algorithm bounded.
    for attempt in range(1, 4):
        structural_defect = bool(
            quality.source_reset_count or quality.repeated_overlap_count or quality.ping_pong_count
        )
        if quality.passed and not structural_defect:
            break
        changed = _repair_ping_pong_detours(beats, runs, evidence_by_id, repairs)
        if changed:
            repairs.append(f"Quality AutoRepair {attempt}/3: mapa semântico reotimizado.")
        # Even without a semantic rebind, rebuilding may create a feasible
        # continuous source lane after earlier repairs.
        runs = _render_runs_from_beats(beats, evidence_by_id, footage, repairs)
        quality = audit_visual_runs(runs, style, repairs, long_form=long_form)
        if not changed and quality.source_reset_count == 0 and quality.repeated_overlap_count == 0:
            break

    # One explicit replay or a necessary hard-event return can be editorially
    # legitimate. The guard remains diagnostic; the ACTUAL rough cut critic is
    # the final authority after all deterministic repairs have been attempted.
    if not quality.passed:
        quality.repairs.append(
            "Quality Guard v6 esgotou reparos determinísticos; o rascunho real será submetido ao crítico global "
            "em vez de interromper o Autopilot."
        )
    return runs, quality


def _range_overlap_ratio(a: VisualRun, b: VisualRun) -> float:
    if a.filename != b.filename:
        return 0.0
    overlap = max(0.0, min(a.source_end_seconds, b.source_end_seconds) - max(a.source_start_seconds, b.source_start_seconds))
    shorter = max(0.01, min(a.source_end_seconds - a.source_start_seconds, b.source_end_seconds - b.source_start_seconds))
    return overlap / shorter


def audit_retention_pacing(
    runs: list[VisualRun],
    beats: list[ProfessionalSyncBeat] | None = None,
    max_static_seconds: float = MAX_STATIC_SCENE_SECONDS,
) -> list[str]:
    """Audit video pacing for retention and narrative continuity (Phase 5.1).

    Ensures no static scene sequence exceeds max_static_seconds (12.0s) without visual variation,
    and checks narrative coherence between consecutive beats.
    """
    warnings: list[str] = []
    for run in runs:
        duration = run.duration
        is_dynamic = (
            getattr(run, "visual_complexity", "medium") == "high"
            or getattr(run, "retention_hook_type", "standard") in {"pattern_interrupt", "visual_proof", "climax"}
            or len(run.event_ids) > 1
            or run.shot_style in {"demo", "action"}
        )
        if duration > max_static_seconds and not is_dynamic:
            warnings.append(
                f"Ritmo de retenção: Tomada {run.run_id} ({duration:.1f}s) excede {max_static_seconds:.1f}s estáticos; "
                "recomendada variação visual, reenquadramento dinâmico ou evidência dirigida."
            )

    if beats and len(beats) > 1:
        for prev_beat, next_beat in zip(beats, beats[1:]):
            prev_words = prev_beat.text.strip().split()
            next_words = next_beat.text.strip().split()
            if prev_words and next_words:
                prev_first = prev_words[0].lower().strip(".,;:?!")
                next_first = next_words[0].lower().strip(".,;:?!")
                if prev_first == next_first and len(prev_first) > 3:
                    warnings.append(
                        f"Coerência narrativa: repetição de termo inicial '{prev_first}' entre batidas consecutivas "
                        f"({prev_beat.beat_id} -> {next_beat.beat_id})."
                    )
    return warnings


def audit_visual_runs(
    runs: list[VisualRun],
    style: EditingStyle,
    repairs: list[str] | None = None,
    *,
    long_form: bool = False,
) -> EditQualityReport:
    repairs = list(repairs or [])
    if not runs:
        return EditQualityReport(passed=False, editing_style=style, warnings=["Nenhuma tomada visual foi criada."])
    total_duration = max(r.output_end_seconds for r in runs) - min(r.output_start_seconds for r in runs)
    cut_count = max(0, len(runs) - 1)
    cuts_per_minute = cut_count * 60 / max(1.0, total_duration)
    short_threshold = 4.5 if long_form else 1.15
    short = sum(1 for r in runs if r.duration < short_threshold)
    resets = 0
    overlaps = 0
    ping_pong = 0
    warnings: list[str] = []
    retention_warnings = audit_retention_pacing(runs, None, max_static_seconds=MAX_STATIC_SCENE_SECONDS)
    warnings.extend(retention_warnings)
    for a, b in zip(runs, runs[1:]):
        shared_event = bool(set(a.event_ids) & set(b.event_ids))
        long_block_replay = (
            a.block_number != b.block_number and shared_event
            and min(a.duration, b.duration) >= 8.0
        )
        if a.filename == b.filename and b.source_start_seconds < a.source_start_seconds - 0.20 and not b.allows_replay and not long_block_replay:
            resets += 1
        if (
            _range_overlap_ratio(a, b) >= 0.55
            and not long_block_replay
            and not (a.allows_replay or b.allows_replay)
        ):
            overlaps += 1
    for a, b, c in zip(runs, runs[1:], runs[2:]):
        if a.filename == c.filename and a.filename != b.filename:
            ping_pong += 1
    max_cpm = ({"demonstrative": 3.0, "balanced": 4.5, "dynamic": 8.0, "auto": 4.5} if long_form else {"demonstrative": 11.0, "balanced": 18.0, "dynamic": 32.0, "auto": 18.0})[style]
    if cuts_per_minute > max_cpm:
        warnings.append(f"Ritmo de corte alto para estilo {style}: {cuts_per_minute:.1f} cortes/min.")
    if short >= 3:
        warnings.append(f"Foram detectadas {short} tomadas com menos de {short_threshold:.2f} s; revisar sensação de picotamento.")
    if resets:
        warnings.append(f"Foram detectados {resets} retornos de timestamp na mesma filmagem sem pedido explícito de replay.")
    if overlaps:
        warnings.append(f"Foram detectadas {overlaps} repetições fortes de trecho entre tomadas consecutivas.")
    if ping_pong:
        warnings.append(f"Foram detectados {ping_pong} padrões A-B-A de troca de cena.")
    # Repeated source/time resets are deterministic editorial defects. A high cut
    # rate alone is style-dependent and therefore only warns.
    passed = resets == 0 and overlaps == 0 and ping_pong <= 1 and short <= max(4, len(runs) // 3)
    if long_form and cuts_per_minute > max_cpm:
        passed = False
    return EditQualityReport(
        passed=passed,
        editing_style=style,
        run_count=len(runs),
        cut_count=cut_count,
        cuts_per_minute=round(cuts_per_minute, 2),
        short_run_count=short,
        source_reset_count=resets,
        repeated_overlap_count=overlaps,
        ping_pong_count=ping_pong,
        warnings=warnings,
        repairs=repairs,
    )
