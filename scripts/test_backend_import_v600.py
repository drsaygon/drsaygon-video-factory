from __future__ import annotations

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import APP_NAME, APP_VERSION
from app.models import ProjectCreate, ProfessionalSyncPlan

assert APP_NAME == "DrSaygon Video Factory", APP_NAME
assert APP_VERSION == "6.0.0", APP_VERSION
assert ProjectCreate(name="Teste original", audio_mode="original").audio_mode == "original"
assert ProjectCreate(name="Teste misto", audio_mode="mixed").audio_mode == "mixed"
assert ProfessionalSyncPlan(total_duration_seconds=0).version.startswith("6"), ProfessionalSyncPlan(total_duration_seconds=0).version
print("[OK] Backend DrSaygon Video Factory 6.0.0 + Audio Mode Engine importados.")
