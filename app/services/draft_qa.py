from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from app.models import EditPlan, ProfessionalSyncPlan, ProjectSettings
from app.services.ffmpeg import concat_videos, mix_narration, render_clip
from app.services.project import ProjectPaths


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
