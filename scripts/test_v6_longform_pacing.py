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


def make_video(path: Path, seconds: int = 90) -> None:
    subprocess.run([
        FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=320x180:r=3:d={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path),
    ], check=True)


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="drsaygon_longform_v6_"))
    try:
        make_video(temp / "A.mp4")
        make_video(temp / "B.mp4")
        beats: list[ProfessionalSyncBeat] = []
        evidence = {
            "Ahold": {"event_id":"Ahold","filename":"A.mp4","start_seconds":2,"end_seconds":80,"description":"demonstracao longa","event_kind":"demo","confidence":.96,"shot_style":"demo","verified_by_user":True},
            "Broll": {"event_id":"Broll","filename":"B.mp4","start_seconds":2,"end_seconds":80,"description":"b-roll generico","confidence":.75,"shot_style":"normal"},
        }
        # 8 blocks x 7 semantic phrases, 7.5 s each = 420 s. Deliberately
        # alternate a generic B-roll on some phrases. v6 should absorb detours.
        cursor = 0.0
        for block in range(1, 9):
            for n in range(1, 8):
                event = "Broll" if n in {3, 5} else "Ahold"
                filename = "B.mp4" if event == "Broll" else "A.mp4"
                beats.append(ProfessionalSyncBeat(
                    beat_id=f"b{block}_{n}", block_number=block, beat_number=n,
                    text=f"explicacao contextual {block}-{n}", required_observation="contexto",
                    output_start_seconds=cursor, output_end_seconds=cursor + 7.5,
                    critical=False, visual_obligation="contextual", timing_source="audio_aligned", timing_confidence=.95,
                    event_id=event, filename=filename, source_start_seconds=10+n, source_end_seconds=17.5+n,
                    evidence_description=event, match_reason="teste", match_confidence=.9,
                    verification="manual" if event == "Ahold" else "semantic",
                    shot_style="demo" if event == "Ahold" else "normal",
                ))
                cursor += 7.5
        runs, report = build_visual_runs(beats, evidence, temp, "auto", long_form=True)
        if report.cuts_per_minute > 5.0:
            raise RuntimeError(f"Ritmo long-form ainda alto: {report.cuts_per_minute} cortes/min; runs={len(runs)}")
        if len(runs) > 16:
            raise RuntimeError(f"Microcortes sobreviveram: {len(runs)} tomadas para 420s")
        print(f"[OK] Long-form v6: 56 batidas -> {len(runs)} tomadas, {report.cuts_per_minute:.2f} cortes/min.")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
