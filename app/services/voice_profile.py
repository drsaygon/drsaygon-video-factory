from __future__ import annotations

import json
from pathlib import Path

from app.config import VOICE_PROFILE_ID, VOICE_PROFILES_ROOT
from app.models import VoiceProfileSettings
from app.services.project import now_iso


def profile_root(profile_id: str = VOICE_PROFILE_ID) -> Path:
    root = VOICE_PROFILES_ROOT / profile_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def settings_path(profile_id: str = VOICE_PROFILE_ID) -> Path:
    return profile_root(profile_id) / "profile.json"


def load_voice_profile(profile_id: str = VOICE_PROFILE_ID) -> VoiceProfileSettings:
    path = settings_path(profile_id)
    if not path.exists():
        return VoiceProfileSettings(profile_id=profile_id)
    return VoiceProfileSettings.model_validate_json(path.read_text(encoding="utf-8"))


def save_voice_profile(settings: VoiceProfileSettings) -> VoiceProfileSettings:
    settings.updated_at = now_iso()
    settings_path(settings.profile_id).write_text(
        json.dumps(settings.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return settings


def default_pronunciations() -> dict[str, str]:
    return {
        "XNB": "xis ene bê",
        "VA": "vê a",
        "USB": "u ésse bê",
        "Wi-Fi": "uái fái",
        "Bluetooth": "blutúti",
        "HDMI": "agá dê eme í",
        "SSD": "ésse ésse dê",
        "GPU": "gê pê u",
        "CPU": "cê pê u",
    }
