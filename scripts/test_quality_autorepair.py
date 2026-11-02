from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import FFMPEG_BIN
from app.models import ProfessionalSyncBeat
from app.services.continuity import build_visual_runs


def make_video(path: Path, seconds: float = 60) -> None:
    subprocess.run([
        FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=320x180:r=5:d={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True)


def b(beat_id: str, block: int, a: float, z: float, filename: str, event: str,
      src_a: float, src_z: float, obligation: str = "contextual", verification: str = "semantic") -> ProfessionalSyncBeat:
    return ProfessionalSyncBeat(
        beat_id=beat_id, block_number=block, beat_number=1, text=beat_id,
        output_start_seconds=a, output_end_seconds=z, required_observation=beat_id,
        critical=obligation == "hard", visual_obligation=obligation,
        timing_source="audio_aligned", timing_confidence=.95,
        event_id=event, filename=filename, source_start_seconds=src_a, source_end_seconds=src_z,
        evidence_description=event, match_reason="teste", match_confidence=.95,
        verification=verification, shot_style="demo" if filename == "A.mp4" else "normal",
    )


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="drsaygon_quality52_"))
    try:
        make_video(temp / "A.mp4", 60)
        make_video(temp / "B.mp4", 20)
        beats = [
            b("a1", 1, 0, 8, "A.mp4", "generic", 2, 10),
            b("a2", 2, 8, 15, "A.mp4", "band", 24, 31, "hard", "manual"),
            # Contextual detour in its own block: classic A-B-A.
            b("detour", 3, 15, 18, "B.mp4", "broll", 1, 4),
            b("a3", 4, 18, 25, "A.mp4", "band", 24, 31, "hard", "manual"),
            # Same file, different evidence id and an overlapping timestamp. Source Lane must fix it.
            b("a4", 5, 25, 34, "A.mp4", "generic2", 8, 17),
        ]
        evidence = {
            "generic": {"event_id":"generic","filename":"A.mp4","start_seconds":0,"end_seconds":55,"description":"tomada longa","confidence":.9,"shot_style":"hold"},
            "generic2": {"event_id":"generic2","filename":"A.mp4","start_seconds":0,"end_seconds":55,"description":"tomada longa 2","confidence":.9,"shot_style":"hold"},
            "band": {"event_id":"band","filename":"A.mp4","start_seconds":20,"end_seconds":45,"description":"fenomeno visivel","event_kind":"phenomenon","visual_phenomena":["banding"],"confidence":1.0,"verified_by_user":True,"shot_style":"demo","evidence_timestamps":[28]},
            "broll": {"event_id":"broll","filename":"B.mp4","start_seconds":0,"end_seconds":10,"description":"broll contextual","confidence":.8,"shot_style":"normal"},
        }
        runs, report = build_visual_runs(beats, evidence, temp, "demonstrative")
        if not report.passed:
            raise RuntimeError(f"Quality Guard ainda bloqueou: {report.model_dump()}")
        if report.repeated_overlap_count or report.source_reset_count or report.ping_pong_count:
            raise RuntimeError(f"Defeitos estruturais sobreviveram: {report.model_dump()}")
        if any(run.filename == "B.mp4" for run in runs):
            raise RuntimeError("A-B-A contextual nao foi autocorrigido")
        # Source clock must move monotonically between every consecutive A run.
        for left, right in zip(runs, runs[1:]):
            if left.filename == right.filename and right.source_start_seconds < left.source_end_seconds - 0.05:
                raise RuntimeError(f"Replay residual: {left.model_dump()} -> {right.model_dump()}")
        print("[OK] Quality AutoRepair v6: A-B-A removido, sobreposicao corrigida e Source Lane monotona.")
        print(f"[OK] {len(beats)} batidas -> {len(runs)} tomadas; Quality Guard passed={report.passed}.")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
