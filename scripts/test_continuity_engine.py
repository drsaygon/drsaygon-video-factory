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


def make_video(path: Path, seconds: float = 40) -> None:
    subprocess.run([
        FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=320x180:r=5:d={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    ], check=True)


def beat(beat_id: str, block: int, n: int, out_a: float, out_b: float, filename: str,
         event: str, verification: str, src_a: float, src_b: float) -> ProfessionalSyncBeat:
    return ProfessionalSyncBeat(
        beat_id=beat_id, block_number=block, beat_number=n, text=beat_id,
        output_start_seconds=out_a, output_end_seconds=out_b, required_observation=beat_id,
        timing_source="audio_aligned", timing_confidence=.95, event_id=event, filename=filename,
        source_start_seconds=src_a, source_end_seconds=src_b, evidence_description=event,
        match_reason="teste", match_confidence=1 if verification == "manual" else .85,
        verification=verification, shot_style="demo" if event == "banding" else "hold",
    )


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="drsaygon_continuity_"))
    try:
        make_video(temp / "close.mp4")
        make_video(temp / "broll.mp4")
        beats = [
            beat("b2_1", 2, 1, 13.9, 16.0, "close.mp4", "banding", "manual", 24, 26),
            beat("b2_2", 2, 2, 16.0, 17.2, "close.mp4", "generic", "semantic", 9, 10.2),
            beat("b2_3", 2, 3, 17.2, 19.9, "broll.mp4", "other", "semantic", 1, 3.7),
            beat("b2_4", 2, 4, 19.9, 24.9, "close.mp4", "banding", "manual", 23, 28),
            beat("b3_1", 3, 1, 24.9, 29.2, "close.mp4", "generic2", "semantic", 8, 12),
            beat("b3_2", 3, 2, 29.2, 31.4, "close.mp4", "banding", "manual", 24, 26),
            beat("b3_3", 3, 3, 31.4, 34.8, "close.mp4", "generic3", "semantic", 8, 12),
            beat("b3_4", 3, 4, 34.8, 38.1, "close.mp4", "banding", "manual", 24, 27),
            beat("b3_5", 3, 5, 38.1, 41.8, "close.mp4", "banding", "manual", 24, 27),
            beat("b3_6", 3, 6, 41.8, 43.1, "close.mp4", "banding", "manual", 24, 26),
            beat("b3_7", 3, 7, 43.1, 45.0, "close.mp4", "banding", "manual", 24, 26),
            beat("b3_8", 3, 8, 45.0, 47.5, "close.mp4", "banding", "manual", 24, 26),
        ]
        evidence = {
            "banding": {"event_id":"banding","filename":"close.mp4","start_seconds":14,"end_seconds":36,
                "description":"faixa / banding visivel","event_kind":"phenomenon","visual_phenomena":["banding"],
                "confidence":1,"verified_by_user":True,"shot_style":"demo","evidence_timestamps":[24.5]},
            "generic": {"event_id":"generic","filename":"close.mp4","start_seconds":0,"end_seconds":37,"description":"luzes","confidence":.8,"shot_style":"hold"},
            "generic2": {"event_id":"generic2","filename":"close.mp4","start_seconds":0,"end_seconds":37,"description":"luzes","confidence":.8,"shot_style":"hold"},
            "generic3": {"event_id":"generic3","filename":"close.mp4","start_seconds":0,"end_seconds":37,"description":"luzes","confidence":.8,"shot_style":"hold"},
            "other": {"event_id":"other","filename":"broll.mp4","start_seconds":0,"end_seconds":7,"description":"b-roll","confidence":.85,"shot_style":"hold"},
        }
        runs, report = build_visual_runs(beats, evidence, temp, "auto")
        if len(runs) != 2:
            raise RuntimeError(f"Esperado 2 tomadas longas; obtido {len(runs)}")
        if not report.passed or report.source_reset_count or report.repeated_overlap_count or report.ping_pong_count:
            raise RuntimeError(f"Quality Guard falhou: {report.model_dump()}")
        if runs[0].beat_ids != ["b2_1", "b2_2", "b2_3", "b2_4"]:
            raise RuntimeError("Desvio B-roll nao foi absorvido pela ancora dominante")
        print("[OK] Continuity Engine: 12 batidas semanticas -> 2 tomadas longas, sem replay/ping-pong estrutural.")
        print(f"[OK] Edit Quality Guard: {report.cuts_per_minute:.1f} cortes/min, passed={report.passed}")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
