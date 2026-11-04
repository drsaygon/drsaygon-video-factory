from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="DrSaygon Local Voice Engine", version="4.0.0")
MODEL = None
MODEL_KEY: str | None = None
MODEL_LOCK = threading.Lock()

# ==============================================================================
# DICIONÁRIO FONÉTICO TÉCNICO BILÍNGUE (EN -> Fonética PT-BR)
# Garante pronúncia impecável de termos técnicos de GPU, IA, Engine e Vídeo
# ==============================================================================
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


def _find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    env_bin = os.getenv("FFMPEG_BIN")
    if env_bin and Path(env_bin).exists():
        return env_bin
    local_bin = Path(__file__).resolve().parents[1] / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if local_bin.exists():
        return str(local_bin)
    return None


def post_process_wav_smart(raw_wav_bytes: bytes, speed: float = 1.0, trim_silence: bool = True) -> bytes:
    """Remove silêncios mortos nas pontas e normaliza o loudness (EBU R128 / -16 LUFS)."""
    ffmpeg_bin = _find_ffmpeg()
    if not ffmpeg_bin:
        return raw_wav_bytes
    filters = []
    if trim_silence:
        filters.append(
            "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB:"
            "stop_periods=-1:stop_duration=0.18:stop_threshold=-45dB"
        )
    if abs(speed - 1.0) > 0.01:
        clamped_speed = max(0.85, min(1.25, speed))
        filters.append(f"atempo={clamped_speed:.3f}")
    filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    af_chain = ",".join(filters)

    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = Path(tmpdir) / "in.wav"
        out_path = Path(tmpdir) / "out.wav"
        in_path.write_bytes(raw_wav_bytes)
        cmd = [
            ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(in_path),
            "-af", af_chain,
            "-ar", "24000", "-ac", "1",
            str(out_path),
        ]
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode == 0 and out_path.exists():
            return out_path.read_bytes()
    return raw_wav_bytes


def _post_process_file_smart(file_path: Path, speed: float = 1.0, trim_silence: bool = True) -> bool:
    ffmpeg_bin = _find_ffmpeg()
    if not ffmpeg_bin or not file_path.exists():
        return False
    filters = []
    if trim_silence:
        filters.append(
            "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB:"
            "stop_periods=-1:stop_duration=0.18:stop_threshold=-45dB"
        )
    if abs(speed - 1.0) > 0.01:
        clamped_speed = max(0.85, min(1.25, speed))
        filters.append(f"atempo={clamped_speed:.3f}")
    filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    af_chain = ",".join(filters)
    tmp_out = file_path.with_suffix(".smart.wav")
    cmd = [
        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(file_path),
        "-af", af_chain,
        "-ar", "24000", "-ac", "1",
        str(tmp_out),
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode == 0 and tmp_out.exists() and tmp_out.stat().st_size > 100:
        shutil.move(str(tmp_out), str(file_path))
        return True
    if tmp_out.exists():
        tmp_out.unlink(missing_ok=True)
    return False


# v3 deliberately favors prosody continuity. v2 split at ; and : and inserted
# long pauses between many fragments, which could sound "picotado" even when
# the cloned timbre itself was good.
QUALITY = {
    "rapida": {"steps": 1, "temp": 0.66, "pause_ms": 70},
    "alta": {"steps": 3, "temp": 0.60, "pause_ms": 90},
    "maxima": {"steps": 5, "temp": 0.52, "pause_ms": 110},
}
LANGUAGE = os.getenv("VOICE_LANGUAGE", os.getenv("DRSAYGON_TTS_LANGUAGE", "portuguese_24l"))
MAX_CONTINUOUS_CHARS = int(os.getenv("DRSAYGON_TTS_CHUNK_CHARS", "430"))


class ExportRequest(BaseModel):
    reference_path: str
    export_path: str


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=6000)
    phonetic_text: str | None = None
    voice_path: str
    output_path: str
    quality: Literal["rapida", "alta", "maxima"] = "alta"
    seed: int = 12345
    speed: float = 1.0
    trim_silence: bool = True


def _package_available() -> bool:
    return importlib.util.find_spec("pocket_tts") is not None


def _load_model(quality: str):
    global MODEL, MODEL_KEY
    if not _package_available():
        raise RuntimeError("pocket-tts nao esta instalado")
    with MODEL_LOCK:
        if MODEL is not None and MODEL_KEY == quality:
            return MODEL
        from pocket_tts import TTSModel
        import torch
        threads = max(2, min(int(os.getenv("DRSAYGON_TTS_THREADS", "4")), os.cpu_count() or 4))
        try:
            torch.set_num_threads(threads)
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass
        cfg = QUALITY[quality]
        MODEL = TTSModel.load_model(
            language=LANGUAGE,
            temp=cfg["temp"],
            lsd_decode_steps=cfg["steps"],
            quantize=False,
        )
        MODEL_KEY = quality
        return MODEL


def _chunks(text: str) -> list[str]:
    """Keep natural clauses together and split only when size makes it necessary."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    if len(text) <= MAX_CONTINUOUS_CHARS:
        return [text]

    # Only true sentence boundaries are preferred. Semicolon/colon stay inside
    # the same synthesis request so intonation remains connected.
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
            current = ""

    for sentence in sentences:
        if len(sentence) > MAX_CONTINUOUS_CHARS:
            # Last-resort split on commas for exceptionally long sentences.
            pieces = [p.strip() for p in re.split(r"(?<=,)\s+", sentence) if p.strip()]
        else:
            pieces = [sentence]
        for piece in pieces:
            candidate = f"{current} {piece}".strip()
            if current and len(candidate) > MAX_CONTINUOUS_CHARS:
                flush()
                current = piece
            else:
                current = candidate
    flush()
    return chunks or [text]


def _trim_edge_silence(audio, sample_rate: int):
    """Trim only long edge silence; internal natural pauses are untouched."""
    import numpy as np
    if audio.size < sample_rate // 4:
        return audio
    absolute = np.abs(audio.astype("float32"))
    peak = float(absolute.max()) if absolute.size else 0.0
    if peak <= 1e-7:
        return audio
    threshold = max(peak * 0.012, 0.00035)
    active = np.flatnonzero(absolute >= threshold)
    if not active.size:
        return audio
    pad = int(sample_rate * 0.035)
    start = max(0, int(active[0]) - pad)
    end = min(audio.size, int(active[-1]) + pad + 1)
    return audio[start:end]


def _edge_fade(audio, sample_rate: int, fade_ms: float = 7.0):
    import numpy as np
    count = min(int(sample_rate * fade_ms / 1000.0), max(0, audio.size // 4))
    if count <= 1:
        return audio
    result = audio.copy()
    ramp = np.linspace(0.0, 1.0, count, dtype="float32")
    result[:count] *= ramp
    result[-count:] *= ramp[::-1]
    return result


@app.get("/health")
def health() -> dict:
    torch_info = ""
    if _package_available():
        try:
            import torch
            torch_info = torch.__version__
        except Exception as error:
            torch_info = f"erro: {error}"
    return {
        "ok": _package_available(),
        "engine": "Kyutai Pocket TTS",
        "engine_version": "4.0.0",
        "language": LANGUAGE,
        "model_loaded": MODEL is not None,
        "quality_loaded": MODEL_KEY,
        "cpu_threads": int(os.getenv("DRSAYGON_TTS_THREADS", "4")),
        "torch": torch_info,
        "prosody_mode": "continuous_chunks",
    }


@app.post("/profile/export")
def export_profile(payload: ExportRequest) -> dict:
    reference = Path(payload.reference_path)
    export_path = Path(payload.export_path)
    if not reference.exists():
        raise HTTPException(status_code=400, detail=f"Amostra de voz nao encontrada: {reference}")
    try:
        model = _load_model("alta")
        from pocket_tts import export_model_state
        state = model.get_state_for_audio_prompt(str(reference), truncate=True)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_model_state(state, str(export_path))
        return {"ok": True, "embedding_path": str(export_path), "engine": "pocket-tts", "language": LANGUAGE}
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@app.post("/generate")
def generate(payload: GenerateRequest) -> dict:
    voice = Path(payload.voice_path)
    output = Path(payload.output_path)
    if not voice.exists():
        raise HTTPException(status_code=400, detail=f"Perfil de voz nao encontrado: {voice}")
    try:
        import numpy as np
        import scipy.io.wavfile
        import torch

        torch.manual_seed(payload.seed)
        model = _load_model(payload.quality)
        state = model.get_state_for_audio_prompt(str(voice), truncate=True)
        cfg = QUALITY[payload.quality]
        text_to_synthesize = normalize_bilingual_tech_text(payload.phonetic_text or payload.text)
        text_chunks = _chunks(text_to_synthesize)
        if not text_chunks:
            raise RuntimeError("Texto vazio depois da normalizacao")

        rendered = []
        chunk_meta = []
        for idx, chunk in enumerate(text_chunks):
            torch.manual_seed(payload.seed + idx * 7919)
            audio = (
                model.generate_audio(state, chunk, copy_state=True)
                .detach().cpu().numpy().astype("float32")
            )
            audio = _trim_edge_silence(audio, model.sample_rate)
            audio = _edge_fade(audio, model.sample_rate)
            if audio.size == 0:
                raise RuntimeError(f"O motor retornou audio vazio no trecho {idx + 1}")
            duration = float(audio.size) / float(model.sample_rate)
            # Gross anomaly gate: a bad generation should fail instead of quietly
            # becoming an obviously broken candidate.
            words = max(1, len(chunk.split()))
            sec_per_word = duration / words
            if sec_per_word < 0.11 or sec_per_word > 1.35:
                raise RuntimeError(
                    f"Geracao de voz inconsistente no trecho {idx + 1}: {sec_per_word:.2f}s/palavra. "
                    "Gere outra alternativa."
                )
            rendered.append(audio)
            chunk_meta.append({"chars": len(chunk), "words": words, "duration_seconds": round(duration, 3)})
            if idx < len(text_chunks) - 1:
                pause_samples = int(model.sample_rate * cfg["pause_ms"] / 1000)
                rendered.append(np.zeros(pause_samples, dtype="float32"))

        full = np.concatenate(rendered) if len(rendered) > 1 else rendered[0]
        peak = float(np.max(np.abs(full))) if full.size else 0.0
        if peak > 0.98:
            full = full * (0.98 / peak)
        output.parent.mkdir(parents=True, exist_ok=True)
        scipy.io.wavfile.write(str(output), model.sample_rate, full)
        duration = float(full.shape[-1]) / float(model.sample_rate)

        # Smart post-processing: remove mortos nas bordas e normaliza loudness (-16 LUFS)
        if payload.trim_silence or abs(payload.speed - 1.0) > 0.01:
            try:
                if _post_process_file_smart(output, speed=payload.speed, trim_silence=payload.trim_silence):
                    _, data = scipy.io.wavfile.read(str(output))
                    duration = float(data.shape[-1]) / float(model.sample_rate)
            except Exception:
                pass

        return {
            "ok": True,
            "output_path": str(output),
            "duration_seconds": round(duration, 3),
            "quality": payload.quality,
            "seed": payload.seed,
            "chunks": len(text_chunks),
            "chunk_meta": chunk_meta,
            "prosody_mode": "continuous_chunks",
            "bilingual_normalized": True,
        }
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
