from __future__ import annotations

import math
import tempfile
import wave
from pathlib import Path

from app.services.ffmpeg import audio_duration, convert_recording_to_wav, safe_speech_bounds

RATE = 48000


def write_fixture(path: Path) -> None:
    # 0.80 s silence + 1.50 s speech-like tone + 0.65 s silence.
    # The tone has gentle 120 ms fade-in/out so the test includes low-level edges
    # that must remain protected by the safety handles.
    lead = 0.80
    body = 1.50
    tail = 0.65
    total = lead + body + tail
    frames = int(total * RATE)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        for i in range(frames):
            t = i / RATE
            amp = 0.0
            if lead <= t < lead + body:
                local = t - lead
                fade = min(1.0, local / 0.12, (body - local) / 0.12)
                amp = 0.55 * max(0.0, fade) * math.sin(2 * math.pi * 220 * local)
            sample = int(max(-1.0, min(1.0, amp)) * 32767)
            wav.writeframesraw(sample.to_bytes(2, byteorder="little", signed=True))


with tempfile.TemporaryDirectory(prefix="drsaygon_v602_trim_") as tmp:
    root = Path(tmp)
    source = root / "manual.wav"
    output = root / "clean.wav"
    write_fixture(source)

    start, end, raw_duration = safe_speech_bounds(source)
    assert 0.50 <= start <= 0.68, (start, end, raw_duration)
    assert 2.45 <= end <= 2.65, (start, end, raw_duration)
    assert 2.90 <= raw_duration <= 3.00

    convert_recording_to_wav(source, output, bounds=(start, end, raw_duration))
    cleaned = audio_duration(output)
    expected = end - start
    assert abs(cleaned - expected) <= 0.04, (cleaned, expected)
    # The output keeps substantial safety handles; it is not a hard gate at the
    # first/last above-threshold sample.
    assert cleaned >= 1.90, cleaned
    assert cleaned <= 2.10, cleaned

print("[OK] Smart Edge Trim v6.0.2 remove silêncio excedente e preserva margens da fala.")
