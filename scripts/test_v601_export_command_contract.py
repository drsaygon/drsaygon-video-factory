from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.services.ffmpeg as media


def extract_arg(command: list[str], name: str) -> str:
    i = command.index(name)
    return command[i + 1]


def main() -> int:
    captured: list[list[str]] = []
    original = media.run_command
    media.run_command = lambda command, **_: captured.append(list(command))  # type: ignore[assignment]
    try:
        temp = Path(tempfile.mkdtemp(prefix="drsaygon_contract_"))
        src = temp / "source.mp4"
        media.export_master(src, temp / "hq.mp4", 30, "premiere_hq")
        hq = captured[-1]
        assert extract_arg(hq, "-b:v") == "50M"
        assert extract_arg(hq, "-maxrate") == "80M"
        assert extract_arg(hq, "-b:a") == "384k"

        media.export_master(src, temp / "max.mp4", 30, "premiere_max")
        max_cmd = captured[-1]
        assert extract_arg(max_cmd, "-b:v") == "80M"
        assert extract_arg(max_cmd, "-maxrate") == "120M"
        assert extract_arg(max_cmd, "-b:a") == "384k"

        media.export_master(src, temp / "hq60.mp4", 60, "premiere_hq")
        hq60 = captured[-1]
        assert extract_arg(hq60, "-b:v") == "70M"
        assert extract_arg(hq60, "-maxrate") == "100M"

        media.export_master(src, temp / "max60.mp4", 60, "premiere_max")
        max60 = captured[-1]
        assert extract_arg(max60, "-b:v") == "100M"
        assert extract_arg(max60, "-maxrate") == "150M"

        media.export_delivery(src, temp / "yt.mp4", "youtube_1080p", 30)
        yt = captured[-1]
        assert extract_arg(yt, "-b:v") == "12M" and extract_arg(yt, "-b:a") == "384k"
        media.export_delivery(src, temp / "social.mp4", "social_1080p", 30)
        social = captured[-1]
        assert extract_arg(social, "-b:v") == "10M" and extract_arg(social, "-b:a") == "256k"

        print("[OK] Contrato de exportação v6.0.2: 50/70, 80/100, YouTube e Social enviados corretamente ao FFmpeg.")
        return 0
    finally:
        media.run_command = original  # type: ignore[assignment]


if __name__ == "__main__":
    raise SystemExit(main())
