from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.config import VOICE_SERVICE_URL


class VoiceServiceError(RuntimeError):
    pass


def _request(path: str, payload: dict[str, Any] | None = None, timeout: int = 3600) -> dict[str, Any]:
    url = f"{VOICE_SERVICE_URL}{path}"
    data = None
    headers = {}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise VoiceServiceError(f"Servico de voz respondeu HTTP {error.code}: {body}") from error
    except Exception as error:
        raise VoiceServiceError(
            "O motor de voz local nao esta respondendo. Execute INSTALAR.bat e abra o programa novamente. "
            f"Detalhe: {error}"
        ) from error


def voice_health() -> dict[str, Any]:
    try:
        import socket
        from urllib.parse import urlparse
        parsed = urlparse(VOICE_SERVICE_URL)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8766
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.35)
            if sock.connect_ex((host, port)) != 0:
                return {"ok": False, "error": f"Motor de voz local nao esta ativo na porta {port}."}
        return _request("/health", timeout=3)
    except Exception as error:
        return {"ok": False, "error": str(error)}


def export_voice(reference_path: Path, embedding_path: Path) -> dict[str, Any]:
    return _request("/profile/export", {
        "reference_path": str(reference_path.resolve()),
        "export_path": str(embedding_path.resolve()),
    })


def generate_voice(text: str, voice_path: Path, output_path: Path, quality: str, seed: int) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return _request("/generate", {
        "text": text,
        "voice_path": str(voice_path.resolve()),
        "output_path": str(output_path.resolve()),
        "quality": quality,
        "seed": seed,
    })


def apply_pronunciations(text: str, rules: dict[str, str]) -> str:
    """Apply explicit channel-specific rules before generic numeric normalization."""
    result = text
    for source in sorted(rules, key=len, reverse=True):
        replacement = rules[source].strip()
        if not source.strip() or not replacement:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(source)}(?!\w)", flags=re.IGNORECASE)
        result = pattern.sub(replacement, result)
    return normalize_numbers_ptbr(result)


def normalize_numbers_ptbr(text: str) -> str:
    """Make Pocket TTS more reliable with PT-BR numbers/units.

    The rules are intentionally conservative: they normalize measurements only when a
    unit follows a number, avoid touching acronyms such as Wi-Fi, and distinguish the
    common Brazilian thousands form (1.440) from short decimal forms (2.4 / 2,4).
    """
    try:
        from num2words import num2words
    except Exception:
        return text

    result = text

    unit_patterns = [
        (r"(?P<n>\d(?:[\d.,]*\d)?)\s*°\s*C\b", r"\g<n> graus Celsius"),
        (r"(?P<n>\d(?:[\d.,]*\d)?)\s*GHz\b", r"\g<n> gigahertz"),
        (r"(?P<n>\d(?:[\d.,]*\d)?)\s*MHz\b", r"\g<n> megahertz"),
        (r"(?P<n>\d(?:[\d.,]*\d)?)\s*kHz\b", r"\g<n> quilohertz"),
        (r"(?P<n>\d(?:[\d.,]*\d)?)\s*Hz\b", r"\g<n> hertz"),
        (r"(?P<n>\d(?:[\d.,]*\d)?)\s*Wh\b", r"\g<n> watts-hora"),
        (r"(?P<n>\d(?:[\d.,]*\d)?)\s*W\b", r"\g<n> watts"),
        (r"(?P<n>\d(?:[\d.,]*\d)?)\s*%", r"\g<n> por cento"),
    ]
    for pattern, replacement in unit_patterns:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    def number_repl(match: re.Match[str]) -> str:
        raw = match.group(0)
        try:
            # Brazilian thousands group: 1.440 / 12.000 / 1.234.567
            if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
                value = int(raw.replace(".", ""))
                return num2words(value, lang="pt_BR") if value <= 9_999_999 else raw

            # Brazilian decimal comma is unambiguous. A single dot with 1-2 digits
            # after it is treated as a decimal too (common in technical model text).
            if "," in raw:
                value = float(raw.replace(".", "").replace(",", "."))
                return num2words(value, lang="pt_BR")
            if re.fullmatch(r"\d+\.\d{1,2}", raw):
                value = float(raw)
                return num2words(value, lang="pt_BR")

            value = int(raw)
            if abs(value) > 9_999_999:
                return raw
            return num2words(value, lang="pt_BR")
        except Exception:
            return raw

    # Do not touch digits glued to letters/slashes; XNB720, URLs and fractions stay intact.
    result = re.sub(
        r"(?<![\w/])(?:\d{1,3}(?:\.\d{3})+|\d{1,7}(?:[.,]\d{1,3})?)(?![\w/])",
        number_repl,
        result,
    )
    return re.sub(r"\s+", " ", result).strip()
