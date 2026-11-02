from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import FFMPEG_BIN, FFPROBE_BIN
from app.services.ffmpeg import export_delivery, export_master


def probe(path: Path) -> dict:
    out = subprocess.check_output([
        FFPROBE_BIN, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)
    ], text=True, encoding="utf-8", errors="replace")
    return json.loads(out)


def validate_identity(path: Path) -> None:
    data = probe(path)
    v = next(s for s in data["streams"] if s.get("codec_type") == "video")
    a = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    format_tags = data.get("format", {}).get("tags", {}) or {}
    stream_tags = v.get("tags", {}) or {}
    for key in ("software", "writing_application", "encoded_by", "tool"):
        if format_tags.get(key) != "DrSaygon Video Factory":
            raise RuntimeError(f"Tag {key} ausente/incorreta em {path.name}: {format_tags}")
    if stream_tags.get("encoder") != "DrSaygon Video Factory":
        raise RuntimeError(f"Encoder do stream incorreto em {path.name}: {stream_tags}")
    if v.get("codec_name") != "h264" or v.get("pix_fmt") != "yuv420p":
        raise RuntimeError(f"Vídeo incompatível em {path.name}: {v.get('codec_name')} {v.get('pix_fmt')}")
    if a and a.get("codec_name") != "aac":
        raise RuntimeError(f"Áudio incompatível em {path.name}: {a.get('codec_name')}")


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="drsaygon_export_v602_"))
    try:
        src = temp / "source.mp4"
        master_hq = temp / "master_hq.mp4"
        master_max = temp / "master_max.mp4"
        yt = temp / "youtube.mp4"
        social = temp / "social.mp4"
        subprocess.run([
            FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=700:sample_rate=48000:duration=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "384k", "-shortest", str(src),
        ], check=True)
        export_master(src, master_hq, 30, "premiere_hq")
        export_master(src, master_max, 30, "premiere_max")
        export_delivery(src, yt, "youtube_1080p", 30)
        export_delivery(src, social, "social_1080p", 30)
        for path in (master_hq, master_max, yt, social):
            validate_identity(path)
        if master_hq.stat().st_size <= yt.stat().st_size:
            raise RuntimeError("Master HQ deveria ser maior que a saída YouTube")
        if master_max.stat().st_size < master_hq.stat().st_size:
            raise RuntimeError("Master Máxima não pode ficar menor que Master HQ no mesmo material")
        print("[OK] Export v6.0.2: HQ/Max/YouTube/Social + H.264/AAC + metadados DrSaygon aprovados.")
        print(f"     tamanhos: max={master_max.stat().st_size} hq={master_hq.stat().st_size} youtube={yt.stat().st_size} social={social.stat().st_size}")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
