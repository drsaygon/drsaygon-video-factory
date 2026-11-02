from __future__ import annotations

import importlib.util
import os
import re
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="DrSaygon Local Voice Engine", version="4.0.0")
MODEL = None
MODEL_KEY: str | None = None
MODEL_LOCK = threading.Lock()

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
    voice_path: str
    output_path: str
    quality: Literal["rapida", "alta", "maxima"] = "alta"
    seed: int = 12345


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
        text_chunks = _chunks(payload.text)
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
        return {
            "ok": True,
            "output_path": str(output),
            "duration_seconds": round(duration, 3),
            "quality": payload.quality,
            "seed": payload.seed,
            "chunks": len(text_chunks),
            "chunk_meta": chunk_meta,
            "prosody_mode": "continuous_chunks",
        }
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
