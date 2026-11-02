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


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="drsaygon_export_v6_"))
    try:
        src = temp / "source.mp4"
        master = temp / "master.mp4"
        yt = temp / "youtube.mp4"
        social = temp / "social.mp4"
        subprocess.run([
            FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=700:sample_rate=48000:duration=3",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "384k", "-shortest", str(src),
        ], check=True)
        export_master(src, master, 30)
        export_delivery(src, yt, "youtube_1080p", 30)
        export_delivery(src, social, "social_1080p", 30)
        for path in (master, yt, social):
            data = probe(path)
            v = next(s for s in data["streams"] if s.get("codec_type") == "video")
            tags = {**(data.get("format", {}).get("tags", {}) or {}), **(v.get("tags", {}) or {})}
            joined = " ".join(str(x) for x in tags.values())
            if "DrSaygon Video Factory" not in joined:
                raise RuntimeError(f"Metadado da aplicação ausente em {path.name}: {tags}")
            format_tags = data.get("format", {}).get("tags", {}) or {}
            stream_tags = v.get("tags", {}) or {}
            for key in ("software", "writing_application", "encoded_by", "tool"):
                if format_tags.get(key) != "DrSaygon Video Factory":
                    raise RuntimeError(f"Tag {key} ausente/incorreta em {path.name}: {format_tags}")
            if stream_tags.get("encoder") != "DrSaygon Video Factory" or stream_tags.get("handler_name") != "DrSaygon Video Factory":
                raise RuntimeError(f"Identidade do stream incorreta em {path.name}: {stream_tags}")
            if v.get("codec_name") != "h264" or v.get("pix_fmt") != "yuv420p":
                raise RuntimeError(f"Perfil incompatível: {path.name} {v.get('codec_name')} {v.get('pix_fmt')}")
        if master.stat().st_size <= yt.stat().st_size:
            raise RuntimeError(f"Master deveria ser maior que YouTube: {master.stat().st_size} <= {yt.stat().st_size}")
        print(f"[OK] Export v6: Master>{yt.name}/Social e metadados DrSaygon Video Factory presentes.")
        print(f"     tamanhos: master={master.stat().st_size} youtube={yt.stat().st_size} social={social.stat().st_size}")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
