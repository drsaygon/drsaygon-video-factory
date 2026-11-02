from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import FFMPEG_BIN
from app.services.ffmpeg import audio_duration, mix_narration, probe_media, run_command


def main() -> None:
    temp = Path(tempfile.mkdtemp(prefix="drsaygon_v6_mixed_"))
    try:
        visual = temp / "visual.mp4"
        narration = temp / "voice.wav"
        output = temp / "mixed.mp4"
        # A source scene with audible 440 Hz camera audio.
        run_command([
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=4",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(visual),
        ])
        # Narration is a separate tone; interval 1-3 s is a FEATURE original-audio scene.
        run_command([
            FFMPEG_BIN, "-y", "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=4",
            "-c:a", "pcm_s16le", str(narration),
        ])
        mix_narration(visual, narration, output, 4.0, feature_intervals=[(1.0, 3.0)])
        info = probe_media(output)
        assert info.has_audio
        assert abs(info.duration - 4.0) < 0.25, info.duration
        assert abs(audio_duration(narration) - 4.0) < 0.1
        print("[OK] Audio misto v6: cena feature preserva audio original e mix executa sem cortar duracao.")
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    main()
