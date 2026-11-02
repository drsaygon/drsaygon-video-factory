from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

from app.config import FFMPEG_BIN, FFPROBE_BIN
from app.services.ffmpeg import create_proxy

ROOT = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory(prefix="drsaygon_v602_player_") as tmp:
    work = Path(tmp)
    source = work / "camera-original.mkv"
    preview = work / "proxy.mp4"
    cmd = [
        FFMPEG_BIN, "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-c:v", "mpeg4", "-q:v", "5", "-c:a", "pcm_s16le", "-shortest", str(source),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr[-2000:]

    create_proxy(source, preview)
    assert preview.exists() and preview.stat().st_size > 1024

    probe = subprocess.run([
        FFPROBE_BIN, "-v", "error", "-show_entries", "stream=codec_type,codec_name,pix_fmt",
        "-of", "json", str(preview),
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    assert probe.returncode == 0, probe.stderr
    streams = json.loads(probe.stdout or "{}").get("streams", [])
    video = next(s for s in streams if s.get("codec_type") == "video")
    audio = next(s for s in streams if s.get("codec_type") == "audio")
    assert video.get("codec_name") == "h264", video
    assert video.get("pix_fmt") == "yuv420p", video
    assert audio.get("codec_name") == "aac", audio

    # Exercise the same FileResponse behavior used by the production route,
    # including byte ranges required for HTML5 seek/scrubbing.
    mini = FastAPI()
    @mini.get("/preview")
    def preview_route() -> FileResponse:
        return FileResponse(preview, media_type="video/mp4", headers={"Cache-Control": "private, max-age=3600"})

    client = TestClient(mini)
    response = client.get("/preview", headers={"Range": "bytes=0-511"})
    assert response.status_code == 206, (response.status_code, response.text[:200])
    assert response.headers.get("content-type", "").startswith("video/mp4")
    assert response.headers.get("content-range", "").startswith("bytes 0-511/")
    assert len(response.content) == 512

main_code = (ROOT / "app/main.py").read_text(encoding="utf-8")
js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
assert '@app.get("/api/projects/{slug}/footage-preview/{filename}")' in main_code
assert 'create_proxy(source, preview)' in main_code
assert '/footage-preview/' in js and 'player.load()' in js
assert 'manualEvidencePlayerStatus' in html and 'playsinline' in html
print("[OK] Evidence Lab v6.0.2: proxy H.264/AAC + HTTP Range/seek + UI compatível confirmados.")
