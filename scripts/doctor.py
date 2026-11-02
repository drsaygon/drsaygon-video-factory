from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def ok(message: str) -> None:
    print(f"[OK] {message}")


def fail(message: str) -> None:
    print(f"[ERRO] {message}")


def warn(message: str) -> None:
    print(f"[AVISO] {message}")


def cmd(command: list[str], label: str, timeout: int = 30) -> bool:
    try:
        process = subprocess.run(command, capture_output=True, text=True, errors="replace", timeout=timeout)
    except Exception as error:
        fail(f"{label}: {error}")
        return False
    if process.returncode != 0:
        fail(f"{label} falhou (codigo {process.returncode}): {(process.stderr or process.stdout)[-1800:]}")
        return False
    lines = (process.stdout or process.stderr).splitlines()
    ok(f"{label}: {lines[0] if lines else 'OK'}")
    return True


def main() -> int:
    print("=" * 76)
    print("DIAGNOSTICO - DRSAYGON VIDEO FACTORY 6.0.2")
    print("=" * 76)
    good = True
    ok(f"Python principal {sys.version.split()[0]}")

    for module in ("fastapi", "uvicorn", "pydantic", "dotenv", "google.genai", "num2words"):
        try:
            __import__(module)
            ok(f"Modulo principal: {module}")
        except Exception as error:
            fail(f"Modulo principal {module}: {error}")
            good = False

    from app.config import APP_NAME, APP_VERSION, FFMPEG_BIN, FFPROBE_BIN
    from app.models import AudioAlignmentPlan, VideoAnalysis, ProjectCreate, ProfessionalSyncPlan

    if APP_NAME == "DrSaygon Video Factory" and APP_VERSION == "6.0.2":
        ok("Identidade do aplicativo: DrSaygon Video Factory 6.0.2")
    else:
        fail(f"Identidade inesperada: {APP_NAME} {APP_VERSION}")
        good = False

    schemas = [VideoAnalysis.model_json_schema(), AudioAlignmentPlan.model_json_schema()]
    if "exclusiveMinimum" in str(schemas):
        fail("Schema ainda contem exclusiveMinimum; a analise Gemini pode falhar")
        good = False
    else:
        ok("Schemas Gemini compativeis: sem exclusiveMinimum")

    try:
        assert ProjectCreate(name="Teste", audio_mode="original").audio_mode == "original"
        assert ProjectCreate(name="Teste", audio_mode="mixed").audio_mode == "mixed"
        assert ProfessionalSyncPlan(total_duration_seconds=0).version.startswith("6")
        ok("Audio Mode Engine + Professional Sync v6 carregados")
    except Exception as error:
        fail(f"Audio Mode Engine / Sync v6: {error}")
        good = False

    try:
        from app.services.continuity import apply_conservative_autopilot_policy, build_visual_runs
        from app.services.ffmpeg import convert_recording_to_wav, export_delivery, export_master
        assert callable(build_visual_runs) and callable(apply_conservative_autopilot_policy)
        assert callable(convert_recording_to_wav) and callable(export_master) and callable(export_delivery)
        ok("Scene Continuity Engine + gravacao sem corte + perfis Master/Delivery carregados")
    except Exception as error:
        fail(f"Motores v6: {error}")
        good = False

    good = cmd([FFMPEG_BIN, "-version"], "FFmpeg") and good
    good = cmd([FFPROBE_BIN, "-version"], "FFprobe") and good

    # Voice engine is optional in v6: original-audio projects can work without it.
    voice_python = ROOT / ".venv_voice" / "Scripts" / "python.exe"
    if not voice_python.exists():
        warn(".venv_voice ausente. Projetos com audio original funcionam; voz local sintetica nao.")
    else:
        voice_test = "import torch,pocket_tts; print('PyTorch '+torch.__version__+' | Pocket TTS OK')"
        if not cmd([str(voice_python), "-c", voice_test], "Motor de voz CPU", timeout=45):
            warn("Voz local indisponivel. Audio original e narracao manual continuam utilizaveis.")

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        warn("GEMINI_API_KEY ausente. Montagem baseada em catalogo salvo ainda pode abrir, mas novas analises IA exigem chave.")
    else:
        ok("Chave Gemini encontrada")

    print("-" * 76)
    if good:
        ok("NUCLEO LOCAL PRONTO. Execute INICIAR.bat.")
        return 0
    fail("Ha pendencias no nucleo local. Copie esta tela para diagnostico.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
