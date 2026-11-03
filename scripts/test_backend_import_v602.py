from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import APP_NAME, APP_VERSION
from app.services.ffmpeg import safe_speech_bounds

assert APP_NAME == "DrSaygon Video Factory"
assert APP_VERSION == "6.0.2"
assert callable(safe_speech_bounds)
print("[OK] Backend DrSaygon Video Factory 6.0.2 + Smart Edge Trim importados.")
