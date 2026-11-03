from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Callable

from app.models import EditPlan, PhoneticBudget, ProfessionalSyncPlan, ProjectSettings, VisualAnchor
from app.services.ffmpeg import concat_videos, mix_narration, render_clip
from app.services.project import ProjectPaths
from app.services.voice_profile import calculate_phonetic_budget, estimate_spoken_duration_sec


class DraftQAError(RuntimeError):
    pass


def render_rough_cut(
    paths: ProjectPaths,
    settings: ProjectSettings,
    plan: EditPlan,
    sync: ProfessionalSyncPlan,
    logger: Callable[[str], None],
) -> Path:
    """Render the exact v5 visual-run edit at low resolution for AI QA."""
    width, height = (360, 640) if settings.format == "short_9_16" else (640, 360)
    work = paths.temp / "rough_cut_qa"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    block_outputs: list[Path] = []
    for block in plan.blocks:
        beats = sorted(
            [b for b in sync.beats if b.block_number == block.number],
            key=lambda b: (b.output_start_seconds, b.beat_number),
        )
        runs = sorted(
            [r for r in sync.visual_runs if r.block_number == block.number],
            key=lambda r: r.output_start_seconds,
        )
        if not beats or not runs:
            continue
        rendered: list[Path] = []
        for idx, run in enumerate(runs, start=1):
            if run.verification == "failed" or not run.filename:
                raise DraftQAError(f"Tomada {run.run_id} sem evidencia para o rascunho")
            source = paths.footage / run.filename
            output = work / f"b{block.number:03d}_run{idx:03d}.mp4"
            render_clip(
                source=source, destination=output, start=run.source_start_seconds, duration=run.duration,
                width=width, height=height, fps=min(30, settings.fps), format_mode=settings.format,
                focus_x=run.focus_x, focus_y=run.focus_y, vertical_strategy=run.vertical_strategy,
                audio_strategy=("mute" if settings.audio_mode == "narration" else ("feature" if settings.audio_mode == "original" and run.audio_strategy != "mute" else run.audio_strategy)),
            )
            rendered.append(output)
        visual = work / f"block_{block.number:03d}_visual.mp4"
        concat_videos(rendered, visual)
        final = work / f"block_{block.number:03d}.mp4"
        narration = paths.narration / f"bloco_{block.number:03d}.wav"
        if settings.audio_mode != "original" and block.needs_voice and narration.exists():
            target = sum(r.duration for r in runs)
            mix_narration(visual, narration, final, target)
        else:
            shutil.copy2(visual, final)
        block_outputs.append(final)

    if not block_outputs:
        raise DraftQAError("Nenhum bloco foi renderizado para o rascunho")
    draft = paths.analysis / "RASCUNHO_QA.mp4"
    concat_videos(block_outputs, draft)
    logger(f"Rascunho editorial QA criado: {draft}")
    return draft



_REPAIR_FILLERS = {
    "realmente", "certamente", "obviamente", "basicamente", "aqui", "entao", "então",
    "podemos", "ver", "notar", "perceber", "portanto", "inclusive", "vale", "lembrar",
    "muito", "bastante", "completamente", "totalmente", "simplesmente", "assim",
}


def audit_and_repair_scene_sync(
    project: Any,
    scene: Any,
    speaking_rate_wps: float = 2.65,
) -> tuple[bool, Any, str]:
    """Surgical auto-repair of a single scene script (Phase 5.2).

    If a scene violates phonetic budget by > 15% or sync_precision_score < 85.0,
    adjusts only that scene's text without touching approved script blocks,
    strictly preserving the visual_anchor trigger word.

    Returns:
        tuple (repaired: bool, scene: Any, reason: str)
    """
    from app.services.professional_sync import resolve_elastic_sync

    text = getattr(scene, "text", None) or getattr(scene, "narration", "")
    if isinstance(scene, dict):
        text = scene.get("text", scene.get("narration", ""))
    text = (text or "").strip()

    duration = 0.0
    if hasattr(scene, "duration"):
        duration = float(scene.duration)
    elif hasattr(scene, "output_end_seconds") and hasattr(scene, "output_start_seconds"):
        duration = float(scene.output_end_seconds - scene.output_start_seconds)
    elif hasattr(scene, "end_seconds") and hasattr(scene, "start_seconds"):
        duration = float(scene.end_seconds - scene.start_seconds)
    elif hasattr(scene, "estimated_seconds"):
        duration = float(scene.estimated_seconds)
    elif isinstance(scene, dict):
        duration = float(scene.get("duration", scene.get("end_seconds", 0.0) - scene.get("start_seconds", 0.0) or scene.get("estimated_seconds", 0.0)))

    if duration <= 0.1:
        duration = 5.0

    budget = getattr(scene, "phonetic_budget", None)
    if isinstance(scene, dict):
        budget = scene.get("phonetic_budget")
    if not budget:
        budget_dict = calculate_phonetic_budget(duration, speaking_rate_wps=speaking_rate_wps)
        budget = PhoneticBudget(**budget_dict)
        if hasattr(scene, "phonetic_budget"):
            scene.phonetic_budget = budget
        elif isinstance(scene, dict):
            scene["phonetic_budget"] = budget

    target_words = getattr(budget, "target_words", 0) if hasattr(budget, "target_words") else budget.get("target_words", 0)
    if target_words <= 0:
        target_words = max(1, round(duration * speaking_rate_wps * 0.88))

    sync_score = getattr(scene, "sync_precision_score", 100.0)
    if isinstance(scene, dict):
        sync_score = scene.get("sync_precision_score", 100.0)

    words = text.split()
    word_count = len(words)
    budget_divergence = abs(word_count - target_words) / max(1, target_words)

    needs_repair = (budget_divergence > 0.15) or (sync_score < 85.0)
    if not needs_repair:
        return False, scene, "Cena em conformidade fonética e de sincronismo."

    # Identify anchor trigger phrase
    anchor = getattr(scene, "visual_anchor", None)
    if isinstance(scene, dict):
        anchor = scene.get("visual_anchor")
    trigger = ""
    if anchor:
        trigger = getattr(anchor, "trigger_phrase", "") if hasattr(anchor, "trigger_phrase") else (anchor.get("trigger_phrase", "") if isinstance(anchor, dict) else "")
        if not trigger:
            trigger = getattr(anchor, "action", "") if hasattr(anchor, "action") else (anchor.get("action", "") if isinstance(anchor, dict) else "")

    # Perform surgical text adjustment
    if word_count > target_words:
        # Condense: remove filler words while keeping trigger
        filtered = []
        for w in words:
            clean_w = w.lower().strip(".,;:?!")
            if clean_w in _REPAIR_FILLERS and len(filtered) + (word_count - len(filtered)) > target_words:
                if trigger and clean_w == trigger.lower().strip(".,;:?!"):
                    filtered.append(w)
                else:
                    continue
            else:
                filtered.append(w)
        # If still too long, take target_words ensuring trigger is preserved
        if len(filtered) > target_words:
            truncated = filtered[:target_words]
            if trigger and not any(trigger.lower().strip(".,;:?!") in tw.lower() for tw in truncated):
                truncated[-1] = trigger
            filtered = truncated
        repaired_text = " ".join(filtered).strip()
        if repaired_text and not repaired_text[-1] in ".?!":
            repaired_text += "."
    else:
        # Expand slightly with descriptive precision
        needed = target_words - word_count
        repaired_text = text
        if needed > 0 and text:
            base = text.rstrip(".?! ")
            if needed == 1:
                repaired_text = f"{base} com precisão."
            elif needed == 2:
                repaired_text = f"{base} com atenção aos detalhes."
            elif needed >= 3:
                repaired_text = f"{base} de forma clara e detalhada neste momento."
            else:
                repaired_text = f"{base}."

    # Apply repaired text
    if hasattr(scene, "text"):
        scene.text = repaired_text
    if hasattr(scene, "narration"):
        scene.narration = repaired_text
    if isinstance(scene, dict):
        if "text" in scene:
            scene["text"] = repaired_text
        if "narration" in scene:
            scene["narration"] = repaired_text

    # Recalculate sync and update scores
    spoken_dur = estimate_spoken_duration_sec(repaired_text, speaking_rate_wps=speaking_rate_wps)
    sync_res = resolve_elastic_sync(duration, spoken_dur, visual_anchor=anchor if isinstance(anchor, VisualAnchor) else None)
    if hasattr(scene, "sync_precision_score"):
        scene.sync_precision_score = sync_res["sync_precision_score"]
        scene.sync_drift_ms = sync_res["sync_drift_ms"]
        scene.applied_sync_strategy = sync_res["strategy"]
    elif isinstance(scene, dict):
        scene["sync_precision_score"] = sync_res["sync_precision_score"]
        scene["sync_drift_ms"] = sync_res["sync_drift_ms"]
        scene["applied_sync_strategy"] = sync_res["strategy"]

    reason = (
        f"Auto-reparo cirúrgico: texto ajustado de {word_count} para {len(repaired_text.split())} palavras "
        f"(alvo: {target_words}, sync score: {sync_res['sync_precision_score']:.1f}%)."
    )
    return True, scene, reason
