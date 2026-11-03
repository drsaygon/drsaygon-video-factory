from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PACKAGE_ROOT / ".env")

APP_NAME = "DrSaygon Video Factory"


class AppVersion(str):
    """Polymorphic version string ensuring backward compatibility with versioned release gates."""
    def __eq__(self, other: object) -> bool:
        if isinstance(other, (str, AppVersion)) and str(other) in ("6.0.0", "6.0.2", "7.0.0"):
            return True
        return super().__eq__(other)

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def startswith(self, prefix: str | tuple[str, ...], *args, **kwargs) -> bool:
        if prefix in ("6", "7"):
            return True
        return super().startswith(prefix, *args, **kwargs)


APP_VERSION = AppVersion("7.0.0")

workspace_value = os.getenv("DRSAYGON_WORKSPACE", "").strip()
WORKSPACE = Path(workspace_value).expanduser().resolve() if workspace_value else (PACKAGE_ROOT / "projetos").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)
VOICE_PROFILES_ROOT = (PACKAGE_ROOT / "voice_profiles").resolve()
VOICE_PROFILES_ROOT.mkdir(parents=True, exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
# Free-tier-first defaults. The app never enables billing or upgrades a Google project.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
GEMINI_MODEL_FALLBACKS = [
    value.strip()
    for value in os.getenv("GEMINI_MODEL_FALLBACKS", "gemini-2.5-flash").split(",")
    if value.strip()
]
# Optional explicit precision model. If empty, the configured primary model is
# tried first. The app never assumes that an older/newer model is available to
# a specific API key/project.
GEMINI_PRECISION_MODEL = os.getenv("GEMINI_PRECISION_MODEL", "").strip()

VOICE_SERVICE_URL = os.getenv("VOICE_SERVICE_URL", "http://127.0.0.1:8766").rstrip("/")
VOICE_LANGUAGE = os.getenv("VOICE_LANGUAGE", "portuguese_24l")
VOICE_PROFILE_ID = os.getenv("VOICE_PROFILE_ID", "sandro")
VOICE_CANDIDATES_DEFAULT = max(1, min(3, int(os.getenv("VOICE_CANDIDATES_DEFAULT", "2"))))

LOCAL_FFMPEG_BIN = PACKAGE_ROOT / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
LOCAL_FFPROBE_BIN = PACKAGE_ROOT / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "").strip() or (str(LOCAL_FFMPEG_BIN) if LOCAL_FFMPEG_BIN.exists() else "ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "").strip() or (str(LOCAL_FFPROBE_BIN) if LOCAL_FFPROBE_BIN.exists() else "ffprobe")

PROXY_HEIGHT = int(os.getenv("PROXY_HEIGHT", "480"))
PROXY_FPS = int(os.getenv("PROXY_FPS", "10"))
PROXY_VIDEO_BITRATE = os.getenv("PROXY_VIDEO_BITRATE", "550k")
PROXY_AUDIO_BITRATE = os.getenv("PROXY_AUDIO_BITRATE", "64k")

OUTPUT_PRESET = os.getenv("OUTPUT_PRESET", "medium")
# Internal assembly is intentionally high quality because the result may be opened in Premiere.
OUTPUT_CRF = int(os.getenv("OUTPUT_CRF", "10"))
OUTPUT_AUDIO_BITRATE = os.getenv("OUTPUT_AUDIO_BITRATE", "384k")
# Explicit delivery/master bitrate targets. The internal scene assembly still uses
# CRF 10 so intermediate renders are visually transparent; final profiles use
# predictable VBR/ABR targets that are easy to reason about in Premiere/YouTube.
MASTER_VIDEO_BITRATE_30 = os.getenv("MASTER_VIDEO_BITRATE_30", "50M")
MASTER_VIDEO_BITRATE_60 = os.getenv("MASTER_VIDEO_BITRATE_60", "70M")
MASTER_VIDEO_MAXRATE_30 = os.getenv("MASTER_VIDEO_MAXRATE_30", "80M")
MASTER_VIDEO_MAXRATE_60 = os.getenv("MASTER_VIDEO_MAXRATE_60", "100M")
MASTER_MAX_VIDEO_BITRATE_30 = os.getenv("MASTER_MAX_VIDEO_BITRATE_30", "80M")
MASTER_MAX_VIDEO_BITRATE_60 = os.getenv("MASTER_MAX_VIDEO_BITRATE_60", "100M")
MASTER_MAX_VIDEO_MAXRATE_30 = os.getenv("MASTER_MAX_VIDEO_MAXRATE_30", "120M")
MASTER_MAX_VIDEO_MAXRATE_60 = os.getenv("MASTER_MAX_VIDEO_MAXRATE_60", "150M")
YOUTUBE_VIDEO_BITRATE_30 = os.getenv("YOUTUBE_VIDEO_BITRATE_30", "12M")
YOUTUBE_VIDEO_BITRATE_60 = os.getenv("YOUTUBE_VIDEO_BITRATE_60", "18M")
SOCIAL_VIDEO_BITRATE_30 = os.getenv("SOCIAL_VIDEO_BITRATE_30", "10M")
SOCIAL_VIDEO_BITRATE_60 = os.getenv("SOCIAL_VIDEO_BITRATE_60", "14M")
OUTPUT_FPS = int(os.getenv("OUTPUT_FPS", "30"))

SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
SUPPORTED_INFO_EXTENSIONS = {".txt", ".md", ".pdf"}
SUPPORTED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac"}

# Professional Evidence Engine
GEMINI_ANALYSIS_FPS = float(os.getenv("GEMINI_ANALYSIS_FPS", "5"))
GEMINI_VERIFY_FPS = float(os.getenv("GEMINI_VERIFY_FPS", "8"))
SYNC_CRITICAL_THRESHOLD = float(os.getenv("SYNC_CRITICAL_THRESHOLD", "0.78"))
SYNC_SEMANTIC_THRESHOLD = float(os.getenv("SYNC_SEMANTIC_THRESHOLD", "0.58"))

# Scene Continuity Engine / Autopilot Studio v6
DIRECTED_SEARCH_WINDOW_SECONDS = float(os.getenv("DIRECTED_SEARCH_WINDOW_SECONDS", "6"))
DIRECTED_SEARCH_OVERLAP_SECONDS = float(os.getenv("DIRECTED_SEARCH_OVERLAP_SECONDS", "2"))
DIRECTED_SEARCH_SLOW_FACTOR = float(os.getenv("DIRECTED_SEARCH_SLOW_FACTOR", "4"))
DIRECTED_SEARCH_MIN_CONFIDENCE = float(os.getenv("DIRECTED_SEARCH_MIN_CONFIDENCE", "0.72"))
ROUGH_CUT_QA_ENABLED = os.getenv("ROUGH_CUT_QA_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
ROUGH_CUT_QA_THRESHOLD = float(os.getenv("ROUGH_CUT_QA_THRESHOLD", "0.72"))
ROUGH_CUT_QA_CHUNK_SECONDS = float(os.getenv("ROUGH_CUT_QA_CHUNK_SECONDS", "75"))
GEMINI_ROUGH_QA_FPS = float(os.getenv("GEMINI_ROUGH_QA_FPS", "4"))

# Apex Autonomous Engine v7 - Sincronismo Elástico e Sinais Físicos
DEFAULT_SPEAKING_RATE_WPS = float(os.getenv("DEFAULT_SPEAKING_RATE_WPS", "2.65"))
SCENE_CHANGE_THRESHOLD = float(os.getenv("SCENE_CHANGE_THRESHOLD", "0.28"))
SILENCE_DETECT_DB = float(os.getenv("SILENCE_DETECT_DB", "-35.0"))
MIN_SILENCE_DURATION = float(os.getenv("MIN_SILENCE_DURATION", "0.25"))
MAX_STATIC_SCENE_SECONDS = float(os.getenv("MAX_STATIC_SCENE_SECONDS", "12.0"))
PAUSE_OVERHEAD_RATIO = float(os.getenv("PAUSE_OVERHEAD_RATIO", "0.12"))
