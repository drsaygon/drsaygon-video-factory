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


def calculate_phonetic_budget(
    duration_sec: float,
    speaking_rate_wps: float = 2.65,
    pause_overhead_ratio: float = 0.12,
) -> dict[str, Any]:
    """Calculate phonetic speech budget for a scene based on natural Brazilian Portuguese cadence."""
    import math
    duration = max(0.5, float(duration_sec))
    effective_speech = max(0.5, duration * (1.0 - pause_overhead_ratio))
    target_words = max(1, round(effective_speech * speaking_rate_wps))
    min_words = max(1, math.floor(target_words * 0.88))
    max_words = max(min_words + 1, math.ceil(target_words * 1.08))
    target_syllables = max(1, round(target_words * 2.35))
    return {
        "scene_duration_sec": round(duration, 3),
        "effective_speech_sec": round(effective_speech, 3),
        "target_words": int(target_words),
        "min_words": int(min_words),
        "max_words": int(max_words),
        "target_syllables": int(target_syllables),
        "speech_rate_wps": round(speaking_rate_wps, 2),
    }


def estimate_spoken_duration_sec(text: str, speaking_rate_wps: float = 2.65) -> float:
    """Estimate spoken duration in seconds including natural respiratory pause overhead."""
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return 0.0
    words = len(cleaned.split())
    duration = float(words) / max(0.5, speaking_rate_wps)
    duration += cleaned.count(",") * 0.18
    duration += cleaned.count(";") * 0.18
    duration += cleaned.count("—") * 0.18
    duration += cleaned.count("--") * 0.18
    ellipsis_count = cleaned.count("...")
    cleaned_no_ellipsis = cleaned.replace("...", "")
    duration += ellipsis_count * 0.35
    duration += cleaned_no_ellipsis.count(".") * 0.35
    duration += cleaned_no_ellipsis.count("?") * 0.35
    duration += cleaned_no_ellipsis.count("!") * 0.35
    return round(duration, 3)
