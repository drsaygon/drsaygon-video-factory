from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models import (
    ClipReference,
    EditPlan,
    ProfessionalSyncBeat,
    ProfessionalSyncPlan,
    ProjectPaths,
    ProjectSettings,
    ScriptBlock,
    VisualRun,
    EditQualityReport,
)
from app.services.assembler import assemble_project
from app.services.ffmpeg import FFMPEG_BIN, probe_media


def run(command):
    process = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if process.returncode:
        raise RuntimeError((process.stderr or process.stdout or "")[-3000:])


def paths_for(root: Path) -> ProjectPaths:
    paths = ProjectPaths(
        root=root,
        footage=root / "01_FILMAGENS",
        information=root / "02_INFORMACOES",
        analysis=root / "03_ANALISE",
        narration=root / "04_NARRACAO",
        result=root / "05_RESULTADO",
        publication=root / "06_PUBLICACAO",
        temp=root / "_TEMP",
        logs=root / "_LOGS",
    )
    for value in paths.model_dump().values():
        Path(value).mkdir(parents=True, exist_ok=True)
    return paths


def make_media(paths: ProjectPaths):
    horizontal = paths.footage / "teste_horizontal.mp4"
    vertical = paths.footage / "teste_vertical.mp4"
    voice = paths.narration / "bloco_001.wav"
    run([
        FFMPEG_BIN, "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=350:sample_rate=48000", "-t", "7",
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(horizontal),
    ])
    run([
        FFMPEG_BIN, "-y", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30", "-t", "7",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vertical),
    ])
    run([
        FFMPEG_BIN, "-y", "-f", "lavfi", "-i", "sine=frequency=260:sample_rate=48000",
        "-t", "6", "-c:a", "pcm_s16le", str(voice),
    ])
    return horizontal, vertical


def one(root: Path, fmt: str):
    if root.exists():
        shutil.rmtree(root)
    paths = paths_for(root)
    horizontal, vertical = make_media(paths)
    settings = ProjectSettings(
        name="Teste local",
        product="Video sintetico",
        objective="Validar montagem Evidence Engine",
        format=fmt,
        desired_minutes=1,
        short_seconds=30,
        slug=root.name,
        created_at="2026-08-09T00:00:00-03:00",
        model="sem-gemini",
        resolution="1080x1920" if fmt == "short_9_16" else "1920x1080",
        fps=30,
    )
    plan = EditPlan(
        project_title="Teste local",
        objective="Validar montagem",
        target_duration_seconds=6,
        blocks=[
            ScriptBlock(
                number=1,
                title="Teste",
                narration="Primeira batida. Segunda batida.",
                estimated_seconds=6,
                pacing="normal",
                clips=[ClipReference(filename=horizontal.name, start_seconds=0, end_seconds=6)],
            )
        ],
    )
    sync = ProfessionalSyncPlan(
        total_duration_seconds=6,
        generated_at="teste",
        ready=True,
        beats=[
            ProfessionalSyncBeat(
                beat_id="b001_v01", block_number=1, beat_number=1, text="Primeira batida.",
                output_start_seconds=0, output_end_seconds=3,
                event_id="e001", filename=horizontal.name,
                source_start_seconds=0, source_end_seconds=3,
                verification="verified", match_confidence=1,
                vertical_strategy="crop", focus_x=0.65, focus_y=0.5,
            ),
            ProfessionalSyncBeat(
                beat_id="b001_v02", block_number=1, beat_number=2, text="Segunda batida.",
                output_start_seconds=3, output_end_seconds=6,
                event_id="e002", filename=vertical.name,
                source_start_seconds=0, source_end_seconds=3,
                verification="verified", match_confidence=1,
                vertical_strategy="fit", focus_x=0.5, focus_y=0.5,
            ),
        ],
        visual_runs=[
            VisualRun(
                run_id="r001", block_number=1, output_start_seconds=0, output_end_seconds=3,
                filename=horizontal.name, source_start_seconds=0, source_end_seconds=3,
                beat_ids=["b001_v01"], event_ids=["e001"], verification="verified", confidence=1,
                vertical_strategy="crop", focus_x=0.65, focus_y=0.5,
            ),
            VisualRun(
                run_id="r002", block_number=1, output_start_seconds=3, output_end_seconds=6,
                filename=vertical.name, source_start_seconds=0, source_end_seconds=3,
                beat_ids=["b001_v02"], event_ids=["e002"], verification="verified", confidence=1,
                vertical_strategy="fit", focus_x=0.5, focus_y=0.5,
            ),
        ],
        edit_quality=EditQualityReport(passed=True, editing_style="balanced", run_count=2, cut_count=1, cuts_per_minute=10),
    )
    final = assemble_project(paths, settings, plan, sync, print, lambda p, m: print(f"{p}% - {m}"))
    info = probe_media(final)
    expected = (1080, 1920) if fmt == "short_9_16" else (1920, 1080)
    if (info.width, info.height) != expected or not 5.7 <= info.duration <= 6.3:
        raise RuntimeError(f"Validacao falhou: {info}")
    print(f"[OK] {fmt}: {info.width}x{info.height}, {info.duration:.2f}s")


def main() -> int:
    print("Teste local v6: Continuity Engine renderer em 16:9 e 9:16 (sem Gemini/Pocket TTS)...")
    one(ROOT / "_TESTE_LOCAL_16_9", "youtube_16_9")
    one(ROOT / "_TESTE_LOCAL_9_16", "short_9_16")
    print("\n[OK] Motor de montagem v6 aprovado nos dois formatos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
