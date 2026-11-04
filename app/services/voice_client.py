from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.config import VOICE_SERVICE_URL


ACRONYM_MAP = {
    r"(?<!\w)C\+\+(?!\w)": "Cê mais mais",
    r"(?<!\w)C#(?!\w)": "Cê xárp",
    r"\bAPIs?\b": lambda m: "A-Pê-Í" if not m.group(0).endswith("s") else "A-Pê-Ís",
    r"\bSDK\b": "Ésse-Dê-Cá",
    r"\bGPUs?\b": lambda m: "Gê-Pê-Ú" if not m.group(0).endswith("s") else "Gê-Pê-Ús",
    r"\bCPUs?\b": lambda m: "Cê-Pê-Ú" if not m.group(0).endswith("s") else "Cê-Pê-Ús",
    r"\bVRAM\b": "Vê-Rã",
    r"\bRAM\b": "Rã",
    r"\bGLSL\b": "Gê-Éle-Ésse-Éle",
    r"\bHLSL\b": "Agá-Éle-Ésse-Éle",
    r"\bDirectX\b": "Dairéct Éks",
    r"\bOpenGL\b": "Ôupen Gê-Éle",
    r"\bFFmpeg\b": "Éfe-Éfe-êm-pég",
    r"\bUI/UX\b": "Iú-Ái Iú-Éks",
    r"\bUI\b": "Iú-Ái",
    r"\bUX\b": "Iú-Éks",
    r"\bLLMs?\b": lambda m: "Éle-Éle-Ême" if not m.group(0).endswith("s") else "Éle-Éle-Êmes",
    r"\bJSON\b": "Djêison",
    r"\bSQL\b": "Ésse-Quê-Éle",
    r"\bHTML\b": "Agá-Tê-Ême-Éle",
    r"\bCSS\b": "Cê-Ésse-Ésse",
}

TECH_PHONETIC_MAP = {
    # Vídeo, Render & GPU
    r"\bshaders?\b": lambda m: "xêider" if not m.group(0).lower().endswith("s") else "xêiders",
    r"\bframe\s*rates?\b": "frêim rêit",
    r"\bframes?\b": lambda m: "frêim" if not m.group(0).lower().endswith("s") else "frêims",
    r"\bkeyframes?\b": lambda m: "kí-frêim" if not m.group(0).lower().endswith("s") else "kí-frêims",
    r"\bbitrates?\b": "bít-rêit",
    r"\bray\s*tracing\b": "rêi trêissin",
    r"\bpath\s*tracing\b": "péf trêissin",
    r"\bupscaling\b": "âp-skêilin",
    r"\bdownscaling\b": "dáun-skêilin",
    r"\banti-aliasing\b": "ânti-êiliassin",
    r"\bmotion\s*blur\b": "môuxon blâr",
    r"\btimeline\b": "táim-láin",
    r"\bviewport\b": "víu-pórt",
    r"\brender\b": "rênder",
    r"\brendering\b": "rênderin",
    r"\bpresets?\b": lambda m: "prí-sét" if not m.group(0).lower().endswith("s") else "prí-séts",
    r"\bplugins?\b": lambda m: "plâguin" if not m.group(0).lower().endswith("s") else "plâguins",
    r"\blookup\s*table\b": "lúk-âp têibol",
    # Engenharia de Software, Arquitetura & IA
    r"\bpipelines?\b": lambda m: "páip-láin" if not m.group(0).lower().endswith("s") else "páip-láins",
    r"\bthreads?\b": lambda m: "tréd" if not m.group(0).lower().endswith("s") else "tréds",
    r"\bmultithreading\b": "mâlti-trédin",
    r"\bbuffers?\b": lambda m: "bâfer" if not m.group(0).lower().endswith("s") else "bâfers",
    r"\bthroughput\b": "tru-pút",
    r"\boverhead\b": "ôver-réd",
    r"\bbenchmarks?\b": lambda m: "bêntch-márk" if not m.group(0).lower().endswith("s") else "bêntch-márks",
    r"\bdeadlocks?\b": "déd-lók",
    r"\bruntime\b": "rân-táim",
    r"\bdeploy\b": "deplói",
    r"\bdeployment\b": "deplóiment",
    r"\bworkflow\b": "uôrk-flôu",
    r"\bframeworks?\b": lambda m: "frêim-uôrk" if not m.group(0).lower().endswith("s") else "frêim-uôrks",
    r"\bopen\s*source\b": "ôupen sôrs",
    r"\bback-?end\b": "bék-ênd",
    r"\bfront-?end\b": "frônt-ênd",
    r"\bfull\s*stack\b": "fúl sték",
    r"\bcommits?\b": lambda m: "comít" if not m.group(0).lower().endswith("s") else "comíts",
    r"\bbranch\b": "brêntch",
    r"\bmerge\b": "mêrdj",
    r"\bpull\s*request\b": "púl rikuést",
    r"\bbuilds?\b": lambda m: "bíld" if not m.group(0).lower().endswith("s") else "bílds",
    r"\bdebug\b": "dibâg",
    r"\bdebugging\b": "dibâguin",
    r"\bfallback\b": "fól-bék",
    r"\bautopilot\b": "óto-páilót",
    r"\bprompts?\b": lambda m: "prômpt" if not m.group(0).lower().endswith("s") else "prômpts",
    r"\btokens?\b": lambda m: "tôuken" if not m.group(0).lower().endswith("s") else "tôukens",
    r"\bfine-?tuning\b": "fáin túnin",
    r"\bembeddings?\b": "embédins",
    r"\bzero-?shot\b": "zíro xót",
    r"\bcode-?switching\b": "côud suítchin",
    r"\bhardware\b": "rárduer",
    r"\bsoftware\b": "sóftuer",
    r"\bfirmware\b": "fârmuer",
    r"\bdrivers?\b": lambda m: "dráiver" if not m.group(0).lower().endswith("s") else "dráivers",
    r"\bcache\b": "kéch",
    r"\bcloud\b": "cláud",
    r"\bservers?\b": lambda m: "sârver" if not m.group(0).lower().endswith("s") else "sârvers",
    r"\bserverless\b": "sârver-lés",
    r"\bstreaming\b": "strímin",
    r"\bparse\b": "pársi",
    r"\bparser\b": "párser",
    r"\bscripts?\b": lambda m: "iskrípt" if not m.group(0).lower().endswith("s") else "iskrípts",
    r"\blayout\b": "lêi-áut",
    r"\bdesign\b": "dizáin",
    r"\bfeatures?\b": lambda m: "fítcher" if not m.group(0).lower().endswith("s") else "fítchers",
    r"\brelease\b": "rilíss",
    r"\bupdates?\b": lambda m: "âp-dêit" if not m.group(0).lower().endswith("s") else "âp-dêits",
    r"\bupgrades?\b": lambda m: "âp-grêid" if not m.group(0).lower().endswith("s") else "âp-grêids",
    r"\bpatch\b": "pétch",
    r"\bbugs?\b": lambda m: "bâg" if not m.group(0).lower().endswith("s") else "bâgs",
    r"\bsetup\b": "sét-âp",
    r"\bsmart\s*trim\b": "ismárt trím",
}


def normalize_bilingual_tech_text(text: str) -> str:
    """Converte siglas e vocabulário técnico em inglês para transcrição fonética PT-BR."""
    normalized = text
    for pattern, repl in ACRONYM_MAP.items():
        normalized = re.sub(pattern, repl, normalized)
    for pattern, repl in TECH_PHONETIC_MAP.items():
        normalized = re.sub(pattern, repl, normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"(\d+)\.(\d+)", r"\1 ponto \2", normalized)
    return normalized


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


def generate_voice(
    text: str,
    voice_path: Path,
    output_path: Path,
    quality: str,
    seed: int,
    phonetic_text: str | None = None,
    speed: float = 1.0,
    trim_silence: bool = True,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "text": text,
        "voice_path": str(voice_path.resolve()),
        "output_path": str(output_path.resolve()),
        "quality": quality,
        "seed": seed,
        "speed": speed,
        "trim_silence": trim_silence,
    }
    if phonetic_text:
        payload["phonetic_text"] = phonetic_text
    return _request("/generate", payload)


def apply_pronunciations(text: str, rules: dict[str, str]) -> str:
    """Apply explicit channel-specific rules before generic numeric normalization."""
    result = text
    for source in sorted(rules, key=len, reverse=True):
        replacement = rules[source].strip()
        if not source.strip() or not replacement:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(source)}(?!\w)", flags=re.IGNORECASE)
        result = pattern.sub(replacement, result)
    result = normalize_bilingual_tech_text(result)
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
