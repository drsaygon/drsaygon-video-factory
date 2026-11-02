from __future__ import annotations

import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import FFMPEG_BIN
from app.services.ffmpeg import audio_duration, convert_recording_to_wav


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="drsaygon_voice_edges_"))
    try:
        source = temp / "manual.wav"
        target = temp / "processed.wav"
        # Tone exists from the FIRST sample through the LAST sample. Any silence
        # trimming would make the duration detectably shorter.
        subprocess.run([
            FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "sine=frequency=620:sample_rate=48000:duration=2.4",
            "-c:a", "pcm_s16le", str(source),
        ], check=True)
        before = audio_duration(source)
        convert_recording_to_wav(source, target)
        after = audio_duration(target)
        if abs(after - before) > 0.035:
            raise RuntimeError(f"A gravacao perdeu bordas: antes={before:.3f}s depois={after:.3f}s")
        code = Path(ROOT / "app/services/ffmpeg.py").read_text(encoding="utf-8")
        fn = code[code.index("def convert_recording_to_wav"):code.index("def prepare_voice_reference")]
        if "silenceremove" in fn:
            raise RuntimeError("convert_recording_to_wav ainda contem silenceremove")
        print(f"[OK] Gravacao manual preservou 100% das bordas: {before:.3f}s -> {after:.3f}s.")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
