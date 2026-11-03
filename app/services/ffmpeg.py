from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import (
    FFMPEG_BIN,
    FFPROBE_BIN,
    OUTPUT_AUDIO_BITRATE,
    OUTPUT_CRF,
    OUTPUT_PRESET,
    MASTER_VIDEO_BITRATE_30,
    MASTER_VIDEO_BITRATE_60,
    MASTER_VIDEO_MAXRATE_30,
    MASTER_VIDEO_MAXRATE_60,
    MASTER_MAX_VIDEO_BITRATE_30,
    MASTER_MAX_VIDEO_BITRATE_60,
    MASTER_MAX_VIDEO_MAXRATE_30,
    MASTER_MAX_VIDEO_MAXRATE_60,
    YOUTUBE_VIDEO_BITRATE_30,
    YOUTUBE_VIDEO_BITRATE_60,
    SOCIAL_VIDEO_BITRATE_30,
    SOCIAL_VIDEO_BITRATE_60,
    PROXY_AUDIO_BITRATE,
    PROXY_FPS,
    PROXY_HEIGHT,
    PROXY_VIDEO_BITRATE,
)


class MediaCommandError(RuntimeError):
    pass


@dataclass(slots=True)
class MediaInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    recorded_at: str = ""


def executable_exists(name: str) -> bool:
    return shutil.which(name) is not None or Path(name).exists()


def run_command(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    if process.returncode != 0:
        stderr = (process.stderr or "").strip()
        raise MediaCommandError(
            f"Comando falhou ({process.returncode}): {' '.join(command)}\n{stderr[-6000:]}"
        )
    return process


def probe_media(path: Path) -> MediaInfo:
    process = run_command([
        FFPROBE_BIN, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)
    ])
    data = json.loads(process.stdout or "{}")
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video_stream:
        raise MediaCommandError(f"Arquivo sem trilha de video: {path}")
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration") or video_stream.get("duration") or 0)
    rate = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "30/1"
    try:
        numerator, denominator = rate.split("/")
        fps = float(numerator) / max(float(denominator), 1.0)
    except Exception:
        fps = 30.0
    format_tags = data.get("format", {}).get("tags", {}) or {}
    video_tags = video_stream.get("tags", {}) or {}
    recorded_at = str(
        format_tags.get("creation_time")
        or video_tags.get("creation_time")
        or format_tags.get("com.apple.quicktime.creationdate")
        or video_tags.get("com.apple.quicktime.creationdate")
        or ""
    ).strip()
    return MediaInfo(
        duration=duration,
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        fps=fps,
        has_audio=audio_stream is not None,
        recorded_at=recorded_at,
    )


def create_proxy(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime:
        return destination
    run_command([
        FFMPEG_BIN, "-y", "-i", str(source),
        "-vf", f"scale=-2:min({PROXY_HEIGHT}\\,ih),fps={PROXY_FPS}",
        "-c:v", "libx264", "-preset", "veryfast", "-b:v", PROXY_VIDEO_BITRATE,
        "-maxrate", PROXY_VIDEO_BITRATE, "-bufsize", "1200k", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", PROXY_AUDIO_BITRATE, "-ac", "1", "-movflags", "+faststart",
        str(destination),
    ])
    return destination


def audio_duration(path: Path) -> float:
    process = run_command([
        FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float((process.stdout or "0").strip())


_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")


def safe_speech_bounds(
    source: Path,
    *,
    threshold_db: float = -46.0,
    minimum_silence: float = 0.22,
    pre_guard: float = 0.18,
    post_guard: float = 0.30,
) -> tuple[float, float, float]:
    """Return conservative edge-trim bounds without touching speech.

    The browser intentionally records a short pre/post roll so codecs never eat
    the first plosive or final syllable. Here we remove only *confirmed edge
    silence* and keep explicit guard handles around detected speech. Internal
    pauses are never touched. If detection is ambiguous, the full recording is
    preserved.
    """
    duration = max(0.0, audio_duration(source))
    if duration <= 0.0:
        return 0.0, 0.0, 0.0

    process = run_command([
        FFMPEG_BIN, "-hide_banner", "-i", str(source),
        "-af", f"silencedetect=noise={threshold_db:g}dB:d={minimum_silence:.3f}",
        "-f", "null", "-",
    ])
    log = process.stderr or ""
    starts = [float(m.group(1)) for m in _SILENCE_START_RE.finditer(log)]
    ends = [float(m.group(1)) for m in _SILENCE_END_RE.finditer(log)]

    start = 0.0
    end = duration

    # Leading silence is trustworthy only when it really begins at the edge.
    if starts and ends and starts[0] <= 0.08 and ends[0] >= minimum_silence:
        candidate = max(0.0, ends[0] - pre_guard)
        if candidate >= 0.12:
            start = candidate

    # Pair the last start/end conservatively. silencedetect emits ordered edge
    # events; a final silence must end essentially at EOF to qualify.
    if starts and ends:
        trailing_start = None
        trailing_end = None
        for silence_start in reversed(starts):
            following = [value for value in ends if value >= silence_start]
            if following:
                possible_end = following[-1]
                if possible_end >= duration - 0.12:
                    trailing_start = silence_start
                    trailing_end = possible_end
                    break
        if trailing_start is not None and trailing_end is not None:
            if duration - trailing_start >= minimum_silence:
                candidate = min(duration, trailing_start + post_guard)
                if duration - candidate >= 0.12:
                    end = candidate

    # Fail-safe: never return an implausibly short body.
    if end - start < 0.50:
        return 0.0, duration, duration
    return round(start, 4), round(end, 4), duration


def convert_recording_to_wav(
    source: Path,
    destination: Path,
    *,
    bounds: tuple[float, float, float] | None = None,
) -> Path:
    """Prepare manual narration with safe edge cleanup.

    v6.0.2 policy: keep the excellent browser noise suppression and the existing
    gentle high/low-pass + loudness normalization, while trimming only verified
    *leading/trailing* silence. A safety handle remains before the first detected
    speech and after the final detected speech. Internal pauses are untouched.
    If silence detection is uncertain, nothing is trimmed.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    start, end, duration = bounds or safe_speech_bounds(source)
    trim_filter = ""
    if duration > 0 and (start > 0.001 or end < duration - 0.001):
        trim_filter = f"atrim=start={start:.4f}:end={end:.4f},asetpts=N/SR/TB,"
    filters = trim_filter + "highpass=f=65,lowpass=f=15000,loudnorm=I=-16:TP=-1.5:LRA=11"
    run_command([
        FFMPEG_BIN, "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "48000",
        "-af", filters,
        "-c:a", "pcm_s16le", str(destination),
    ])
    return destination


def prepare_voice_reference(source: Path, destination: Path) -> Path:
    """Mild cleanup only: Pocket TTS reproduces the reference's acoustic character."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_command([
        FFMPEG_BIN, "-y", "-i", str(source), "-t", "30", "-vn", "-ac", "1", "-ar", "24000",
        "-af",
        "highpass=f=70,lowpass=f=11500,afftdn=nr=6:nf=-38,"
        "silenceremove=start_periods=1:start_duration=0.12:start_threshold=-48dB,"
        "areverse,"
        "silenceremove=start_periods=1:start_duration=0.12:start_threshold=-48dB,"
        "areverse,"
        "loudnorm=I=-18:TP=-2:LRA=10",
        "-c:a", "pcm_s16le", str(destination),
    ])
    return destination


def normalize_voice_audio(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_command([
        FFMPEG_BIN, "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "48000",
        "-af", "highpass=f=65,lowpass=f=15000,loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:a", "pcm_s16le", str(destination),
    ])
    return destination


def _video_filter(
    width: int,
    height: int,
    fps: int,
    format_mode: str,
    focus_x: float,
    focus_y: float,
    vertical_strategy: str,
) -> tuple[str, bool]:
    fx = max(0.0, min(1.0, focus_x))
    fy = max(0.0, min(1.0, focus_y))
    if format_mode != "short_9_16":
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p"
        )
        return vf, False

    if vertical_strategy == "blur":
        complex_filter = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:"
            f"x='max(0,min(iw-ow,iw*{fx:.4f}-ow/2))':"
            f"y='max(0,min(ih-oh,ih*{fy:.4f}-oh/2))',"
            "boxblur=20:8[bg];"
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps},format=yuv420p[v]"
        )
        return complex_filter, True

    if vertical_strategy == "fit":
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p"
        )
        return vf, False

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:"
        f"x='max(0,min(iw-ow,iw*{fx:.4f}-ow/2))':"
        f"y='max(0,min(ih-oh,ih*{fy:.4f}-oh/2))',"
        f"setsar=1,fps={fps},format=yuv420p"
    )
    return vf, False


def render_clip(
    source: Path,
    destination: Path,
    start: float,
    duration: float,
    width: int,
    height: int,
    fps: int,
    format_mode: str,
    focus_x: float = 0.5,
    focus_y: float = 0.5,
    vertical_strategy: str = "crop",
    audio_strategy: str = "ambient",
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    info = probe_media(source)
    safe_start = max(0.0, start)
    safe_duration = max(0.15, min(duration, max(0.15, info.duration - safe_start)))
    filter_value, is_complex = _video_filter(
        width, height, fps, format_mode, focus_x, focus_y, vertical_strategy
    )
    volume = {"mute": 0.0, "ambient": 0.12, "feature": 1.0}.get(audio_strategy, 0.12)

    command = [FFMPEG_BIN, "-y", "-ss", f"{safe_start:.3f}", "-i", str(source)]
    audio_input_index = 0
    if not info.has_audio:
        command += ["-f", "lavfi", "-t", f"{safe_duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        audio_input_index = 1
    command += ["-t", f"{safe_duration:.3f}"]

    if is_complex:
        command += ["-filter_complex", filter_value, "-map", "[v]"]
    else:
        command += ["-vf", filter_value, "-map", "0:v:0"]

    command += [
        "-map", f"{audio_input_index}:a:0",
        "-af", f"volume={volume:.4f},aresample=48000,apad,atrim=0:{safe_duration:.3f}",
        "-c:v", "libx264", "-preset", OUTPUT_PRESET, "-crf", str(OUTPUT_CRF),
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-g", str(max(24, fps)),
        "-c:a", "aac", "-b:a", OUTPUT_AUDIO_BITRATE, "-ar", "48000", "-ac", "2",
        "-metadata:s:v:0", "encoder=DrSaygon Video Factory",
        "-metadata:s:v:0", "handler_name=DrSaygon Video Factory",
        "-metadata", "software=DrSaygon Video Factory",
        "-metadata", "writing_application=DrSaygon Video Factory",
        "-metadata", "encoded_by=DrSaygon Video Factory",
        "-metadata", "comment=Created by DrSaygon Video Factory",
        "-movflags", "+faststart+use_metadata_tags", "-shortest", str(destination),
    ]
    run_command(command)
    return destination


def concat_videos(inputs: list[Path], destination: Path) -> Path:
    if not inputs:
        raise MediaCommandError("Nenhum video para concatenar")
    destination.parent.mkdir(parents=True, exist_ok=True)
    list_file = destination.with_suffix(".concat.txt")
    lines = []
    for path in inputs:
        escaped = str(path.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines), encoding="utf-8")
    try:
        run_command([
            FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", "-movflags", "+faststart", str(destination),
        ])
    finally:
        list_file.unlink(missing_ok=True)
    return destination


def _narration_volume_filter(feature_intervals: list[tuple[float, float]] | None) -> str:
    """Duck narration while a scene's original audio is explicitly featured."""
    intervals = [(max(0.0, a), max(0.0, b)) for a, b in (feature_intervals or []) if b > a]
    if not intervals:
        return "volume=1"
    expr = "1"
    # Nest if() from the end so every marked interval drops voice to 8%.
    for start, end in reversed(intervals):
        expr = f"if(between(t,{start:.3f},{end:.3f}),0.08,{expr})"
    return f"volume='{expr}':eval=frame"


def mix_narration(
    visual: Path,
    narration: Path,
    destination: Path,
    duration: float,
    feature_intervals: list[tuple[float, float]] | None = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    voice_filter = _narration_volume_filter(feature_intervals)
    run_command([
        FFMPEG_BIN, "-y", "-i", str(visual), "-i", str(narration),
        "-filter_complex",
        "[0:a]aresample=48000[amb];"
        f"[1:a]aresample=48000,pan=stereo|c0=c0|c1=c0,{voice_filter}[voice];"
        "[amb][voice]amix=inputs=2:duration=first:dropout_transition=0,"
        "alimiter=limit=0.95[a]",
        "-map", "0:v:0", "-map", "[a]", "-t", f"{duration:.3f}",
        "-c:v", "copy", "-c:a", "aac", "-b:a", OUTPUT_AUDIO_BITRATE,
        "-metadata:s:v:0", "encoder=DrSaygon Video Factory",
        "-metadata:s:v:0", "handler_name=DrSaygon Video Factory",
        "-metadata", "software=DrSaygon Video Factory",
        "-metadata", "writing_application=DrSaygon Video Factory",
        "-metadata", "encoded_by=DrSaygon Video Factory",
        "-metadata", "comment=Created by DrSaygon Video Factory",
        "-movflags", "+faststart+use_metadata_tags", str(destination),
    ])
    return destination


def _video_factory_metadata_args() -> list[str]:
    """Metadata that identifies the creator application as far as MP4 allows.

    The MP4 muxer keeps its own low-level Lavf encoder tag, so no program can
    truthfully guarantee what a hosting platform will display. These tags are
    explicit and ffprobe-visible and give importers/hosts the strongest honest
    signal available without falsifying container internals.
    """
    return [
        "-metadata:s:v:0", "encoder=DrSaygon Video Factory",
        "-metadata:s:v:0", "handler_name=DrSaygon Video Factory",
        "-metadata:s:a:0", "handler_name=DrSaygon Video Factory Audio",
        "-metadata", "software=DrSaygon Video Factory",
        "-metadata", "writing_application=DrSaygon Video Factory",
        "-metadata", "encoded_by=DrSaygon Video Factory",
        "-metadata", "tool=DrSaygon Video Factory",
        "-metadata", "creator_tool=DrSaygon Video Factory",
        "-metadata", "creation_tool=DrSaygon Video Factory",
        "-metadata", "com.apple.quicktime.software=DrSaygon Video Factory",
        "-metadata", "comment=Created by DrSaygon Video Factory / Saygonsoft",
    ]


def stamp_video_factory_metadata(source: Path, destination: Path) -> Path:
    """Remux a file while stamping Video Factory metadata without re-encoding."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_command([
        FFMPEG_BIN, "-y", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
        *_video_factory_metadata_args(),
        "-movflags", "+faststart+use_metadata_tags", str(destination),
    ])
    return destination


def _rate_for_fps(normal: str, high: str, fps: int) -> str:
    return high if fps > 30 else normal


def export_master(source: Path, destination: Path, fps: int, profile: str = "premiere_hq") -> Path:
    """Create a high-bitrate H.264 MP4 master intended for Premiere/final finishing.

    ``premiere_hq`` targets ~50/70 Mb/s and is the recommended balance.
    ``premiere_max`` targets ~80/100 Mb/s for an even heavier intermediate.
    Both preserve 48 kHz stereo audio at 384 kb/s and Video Factory metadata.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if profile == "premiere_hq":
        bitrate = _rate_for_fps(MASTER_VIDEO_BITRATE_30, MASTER_VIDEO_BITRATE_60, fps)
        maxrate = _rate_for_fps(MASTER_VIDEO_MAXRATE_30, MASTER_VIDEO_MAXRATE_60, fps)
        bufsize = "160M" if fps > 30 else "120M"
    elif profile == "premiere_max":
        bitrate = _rate_for_fps(MASTER_MAX_VIDEO_BITRATE_30, MASTER_MAX_VIDEO_BITRATE_60, fps)
        maxrate = _rate_for_fps(MASTER_MAX_VIDEO_MAXRATE_30, MASTER_MAX_VIDEO_MAXRATE_60, fps)
        bufsize = "240M" if fps > 30 else "180M"
    else:
        raise MediaCommandError(f"Perfil Master desconhecido: {profile}")
    run_command([
        FFMPEG_BIN, "-y", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "slow", "-b:v", bitrate,
        "-maxrate", maxrate, "-bufsize", bufsize,
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-g", str(max(24, fps * 2)), "-keyint_min", str(max(12, fps)),
        "-c:a", "aac", "-b:a", "384k", "-ar", "48000", "-ac", "2",
        *_video_factory_metadata_args(),
        "-movflags", "+faststart+use_metadata_tags", str(destination),
    ])
    return destination

def export_delivery(source: Path, destination: Path, profile: str, fps: int) -> Path:
    """Create a predictable high-quality upload file from the internal master.

    Both profiles use MP4/H.264 High, progressive 4:2:0, AAC 48 kHz and Fast
    Start. The YouTube profile favors quality; Social is slightly smaller while
    remaining robust for mainstream platforms.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if profile == "youtube_1080p":
        bitrate = _rate_for_fps(YOUTUBE_VIDEO_BITRATE_30, YOUTUBE_VIDEO_BITRATE_60, fps)
        maxrate = "26M" if fps > 30 else "18M"
        bufsize = "36M" if fps > 30 else "24M"
        audio_bitrate = "384k"
    elif profile == "social_1080p":
        bitrate = _rate_for_fps(SOCIAL_VIDEO_BITRATE_30, SOCIAL_VIDEO_BITRATE_60, fps)
        maxrate = "20M" if fps > 30 else "14M"
        bufsize = "28M" if fps > 30 else "20M"
        audio_bitrate = "256k"
    else:
        raise MediaCommandError(f"Perfil de entrega desconhecido: {profile}")
    run_command([
        FFMPEG_BIN, "-y", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "slow", "-b:v", bitrate,
        "-maxrate", maxrate, "-bufsize", bufsize,
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-g", str(max(24, fps * 2)), "-keyint_min", str(max(12, fps)),
        "-c:a", "aac", "-b:a", audio_bitrate, "-ar", "48000", "-ac", "2",
        *_video_factory_metadata_args(),
        "-movflags", "+faststart+use_metadata_tags", str(destination),
    ])
    return destination

def trim_video(source: Path, destination: Path, duration: float) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_command([
        FFMPEG_BIN, "-y", "-i", str(source), "-t", f"{duration:.3f}",
        "-c:v", "copy", "-c:a", "copy", str(destination),
    ])
    return destination


def create_qa_slice(
    source: Path,
    destination: Path,
    start: float,
    end: float,
) -> Path:
    """Create a compact low-res slice of the actual rough cut WITH narration.

    Long-form QA is chunked so a 10+ minute edit does not become one huge
    multimodal request. The slice preserves the exact audio/video timing.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    info = probe_media(source)
    safe_start = max(0.0, min(float(start), info.duration))
    safe_end = max(safe_start + 0.25, min(float(end), info.duration))
    duration = max(0.25, safe_end - safe_start)
    run_command([
        FFMPEG_BIN, "-y", "-ss", f"{safe_start:.3f}", "-i", str(source),
        "-t", f"{duration:.3f}",
        "-vf", "scale='min(480,iw)':-2,setsar=1,fps=24,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
        "-c:a", "aac", "-b:a", "96k", "-ar", "48000",
        "-movflags", "+faststart", str(destination),
    ])
    return destination


def create_verification_proxy(
    source: Path,
    destination: Path,
    start: float,
    end: float,
) -> Path:
    """Create a small full-frame micro-clip for the adversarial Gemini verifier.

    It intentionally avoids vertical cropping so the verifier sees all available
    evidence. The filename is internal/ASCII-safe, which also avoids the Windows/
    multipart filename encoding problem seen with accented source names.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    info = probe_media(source)
    safe_start = max(0.0, min(float(start), info.duration))
    safe_end = max(safe_start + 0.2, min(float(end), info.duration))
    duration = max(0.2, safe_end - safe_start)
    run_command([
        FFMPEG_BIN, "-y", "-ss", f"{safe_start:.3f}", "-i", str(source),
        "-t", f"{duration:.3f}",
        "-vf", "scale='min(720,iw)':-2,fps=12,setsar=1,format=yuv420p",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
        "-movflags", "+faststart", str(destination),
    ])
    return destination


def create_slow_search_proxy(
    source: Path,
    destination: Path,
    start: float,
    end: float,
    slow_factor: float = 4.0,
) -> Path:
    """Create a slowed, full-frame clip for targeted visual searches.

    Gemini's normal video sampling can miss quick visual changes. Slowing the
    micro-clip makes a short event occupy more sampled frames while keeping a
    deterministic mapping back to source time.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    info = probe_media(source)
    safe_start = max(0.0, min(float(start), info.duration))
    safe_end = max(safe_start + 0.25, min(float(end), info.duration))
    duration = safe_end - safe_start
    factor = max(1.0, min(float(slow_factor), 8.0))
    run_command([
        FFMPEG_BIN, "-y", "-ss", f"{safe_start:.3f}", "-i", str(source),
        "-t", f"{duration:.3f}",
        "-vf", f"setpts={factor:.4f}*PTS,scale='min(960,iw)':-2,fps=24,setsar=1,format=yuv420p",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-movflags", "+faststart", str(destination),
    ])
    return destination


def extract_grayscale_frames(
    source: Path,
    start: float,
    end: float,
    width: int = 96,
    height: int = 54,
    fps: int = 24,
) -> tuple[bytes, int, int, int]:
    """Return downscaled grayscale raw frames for cheap local visual scoring."""
    info = probe_media(source)
    safe_start = max(0.0, min(float(start), info.duration))
    safe_end = max(safe_start + 0.2, min(float(end), info.duration))
    duration = safe_end - safe_start
    command = [
        FFMPEG_BIN, "-v", "error", "-ss", f"{safe_start:.3f}", "-i", str(source),
        "-t", f"{duration:.3f}", "-vf", f"scale={width}:{height},fps={fps},format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    if process.returncode != 0:
        raise MediaCommandError((process.stderr or b"").decode("utf-8", errors="replace")[-4000:])
    return process.stdout or b"", width, height, fps



_SCDET_RE = re.compile(r"lavfi\.scd\.score:\s*([0-9.]+).*?lavfi\.scd\.time:\s*([0-9.]+)")
_VOL_MEAN_RE = re.compile(r"mean_volume:\s*(-?[0-9.]+)\s*dB")
_VOL_MAX_RE = re.compile(r"max_volume:\s*(-?[0-9.]+)\s*dB")
_SILENCE_START_NUM_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_NUM_RE = re.compile(r"silence_end:\s*([0-9.]+)")


def extract_physical_media_map(
    video_path: str | Path,
    scene_threshold: float = 0.28,
    silence_db: float = -35.0,
    min_silence_sec: float = 0.25,
) -> dict[str, Any]:
    """Deterministic physical analysis of video and audio tracks via FFmpeg.

    Extracts visual cut boundaries, audio silence windows, volume levels,
    and calculates surgical cut points ('Cortes Ouro') before AI processing.
    """
    path = Path(video_path)
    duration = 0.0
    has_audio = True

    try:
        info = probe_media(path)
        duration = max(0.0, float(info.duration))
        has_audio = bool(info.has_audio)
    except Exception:
        try:
            duration = max(0.0, audio_duration(path))
        except Exception:
            duration = 10.0

    cut_points: list[dict[str, Any]] = []

    # 1. Visual cuts detection via scdet
    try:
        scd_proc = subprocess.run(
            [
                FFMPEG_BIN, "-hide_banner", "-nostats", "-i", str(path),
                "-vf", f"scdet=threshold={scene_threshold:.3f}:sc_pass=1",
                "-an", "-f", "null", "-",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        scd_log = scd_proc.stderr or ""
        for m in _SCDET_RE.finditer(scd_log):
            score_val = float(m.group(1))
            norm_score = min(1.0, score_val if score_val <= 1.0 else score_val / 100.0)
            time_val = float(m.group(2))
            if 0.0 <= time_val <= duration + 0.5:
                cut_points.append({
                    "timestamp": round(time_val, 3),
                    "score": round(norm_score, 3),
                    "cut_type": "scene_change",
                })
    except Exception:
        pass

    # 2. Audio silences & volume levels detection
    silence_windows: list[dict[str, Any]] = []
    mean_volume_db = -20.0
    max_volume_db = 0.0

    if has_audio:
        try:
            sil_proc = subprocess.run(
                [
                    FFMPEG_BIN, "-hide_banner", "-nostats", "-i", str(path),
                    "-af", f"silencedetect=noise={silence_db:g}dB:d={min_silence_sec:.3f},volumedetect",
                    "-vn", "-f", "null", "-",
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            sil_log = sil_proc.stderr or ""
            starts = [float(m.group(1)) for m in _SILENCE_START_NUM_RE.finditer(sil_log)]
            ends = [float(m.group(1)) for m in _SILENCE_END_NUM_RE.finditer(sil_log)]

            for s_in in starts:
                f_ends = [e for e in ends if e >= s_in]
                s_out = f_ends[0] if f_ends else duration
                silence_windows.append({
                    "start": round(s_in, 3),
                    "end": round(s_out, 3),
                    "duration": round(max(0.0, s_out - s_in), 3),
                })

            vol_mean_match = _VOL_MEAN_RE.search(sil_log)
            if vol_mean_match:
                mean_volume_db = float(vol_mean_match.group(1))

            vol_max_match = _VOL_MAX_RE.search(sil_log)
            if vol_max_match:
                max_volume_db = float(vol_max_match.group(1))
        except Exception:
            pass

    # 3. Surgical safe cut points ('Cortes Ouro')
    safe_cuts: set[float] = set()
    pad = 0.15

    for cp in cut_points:
        tv = cp["timestamp"]
        paired = False
        for sw in silence_windows:
            sin = sw["start"]
            sout = sw["end"]
            if sin <= tv <= sout:
                safe_cuts.add(round(tv, 3))
                paired = True
                break
            if abs(tv - sin) <= 0.35:
                gold = max(0.0, min(duration, sin + pad))
                safe_cuts.add(round(gold, 3))
                paired = True
                break
            if abs(tv - sout) <= 0.35:
                gold = max(0.0, min(duration, max(sin, sout - pad)))
                safe_cuts.add(round(gold, 3))
                paired = True
                break
        if not paired and (not has_audio or not silence_windows):
            safe_cuts.add(round(tv, 3))

    # Safe deterministic fallback if no cuts detected or offline synthetic test
    if not safe_cuts:
        step = 5.0
        cur = step
        while cur < (duration if duration > 1.0 else 10.0):
            safe_cuts.add(round(cur, 3))
            cur += step

    safe_cut_timestamps = sorted([c for c in safe_cuts if 0.0 < c < (duration if duration > 0.5 else 999999.0)])
    if not safe_cut_timestamps and duration > 1.0:
        safe_cut_timestamps = [round(min(5.0, duration / 2.0), 3)]

    return {
        "duration": round(duration, 3),
        "cut_points": cut_points,
        "silence_windows": silence_windows,
        "mean_volume_db": round(mean_volume_db, 2),
        "max_volume_db": round(max_volume_db, 2),
        "safe_cut_timestamps": safe_cut_timestamps,
    }


def apply_snap_to_cut(timestamp: float, safe_cuts: list[float], threshold: float = 0.40) -> float:
    """Snap a timestamp to the closest physical safe cut point within threshold distance."""
    if not safe_cuts:
        return round(float(timestamp), 3)
    best_diff = float(threshold) + 0.001
    snapped = float(timestamp)
    for cut in safe_cuts:
        diff = abs(timestamp - cut)
        if diff <= threshold and diff < best_diff:
            best_diff = diff
            snapped = cut
    return round(snapped, 3)


def apply_audio_tempo(input_wav: Path, output_wav: Path, tempo_factor: float) -> Path:
    """Apply micro-tempo adjustment to speech audio via FFmpeg atempo (0.96 - 1.05)."""
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    clamped_tempo = max(0.5, min(2.0, float(tempo_factor)))
    run_command([
        FFMPEG_BIN, "-y", "-i", str(input_wav),
        "-af", f"atempo={clamped_tempo:.4f}",
        "-c:a", "pcm_s16le", str(output_wav),
    ])
    return output_wav
