from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TESTS = [
    "test_backend_import_v600.py",
    "test_v6_manual_recording_edges.py",
    "test_v6_original_audio_mode.py",
    "test_v6_mixed_audio.py",
    "test_v6_scene_audio_overrides.py",
    "test_v6_longform_pacing.py",
    "test_quality_autorepair.py",
    "test_continuity_engine.py",
    "test_v6_export_profiles_metadata.py",
    "test_project_duration.py",
    "test_generic_publication_engine.py",
    "test_publication_studio.py",
    "test_adaptive_description_composer.py",
    "test_autopilot_reliability.py",
]


def fail(message: str) -> int:
    print(f"[ERRO] {message}")
    return 1


def static_gate() -> None:
    # Compile source without creating __pycache__.
    py_files = [p for base in (ROOT / "app", ROOT / "voice", ROOT / "scripts") for p in base.rglob("*.py")]
    for path in py_files:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    from app.config import APP_NAME, APP_VERSION
    if APP_NAME != "DrSaygon Video Factory" or APP_VERSION != "6.0.0":
        raise RuntimeError(f"Identidade inesperada: {APP_NAME} {APP_VERSION}")

    ffmpeg_code = (ROOT / "app/services/ffmpeg.py").read_text(encoding="utf-8")
    assembler_code = (ROOT / "app/services/assembler.py").read_text(encoding="utf-8")
    sync_code = (ROOT / "app/services/professional_sync.py").read_text(encoding="utf-8")
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    continuity = (ROOT / "app/services/continuity.py").read_text(encoding="utf-8")

    required = {
        "manual recording edge preservation": "def convert_recording_to_wav",
        "master exporter": "def export_master",
        "delivery exporter": "def export_delivery",
        "Video Factory metadata": "writing_application=DrSaygon Video Factory",
    }
    for label, marker in required.items():
        if marker not in ffmpeg_code:
            raise RuntimeError(f"Recurso ausente ({label}): {marker}")
    manual_fn = ffmpeg_code[ffmpeg_code.index("def convert_recording_to_wav"):ffmpeg_code.index("def prepare_voice_reference")]
    if "silenceremove" in manual_fn:
        raise RuntimeError("Gravacao manual ainda contem silenceremove")
    if "effective_audio = run.audio_strategy" not in assembler_code:
        raise RuntimeError("Assembler nao respeita audio por tomada")
    if '"mute" if settings.audio_mode == "narration"' not in sync_code:
        raise RuntimeError("Modo narracao nao nasce com audio original mutado")
    if "FALE AGORA" not in js or "preservando o fim da sua voz" not in js:
        raise RuntimeError("UI de pre-roll/pos-roll da gravacao manual ausente")
    for marker in ("projectAudioMode", "Áudio original das filmagens", "deliveryProfile", "MASTER_DRSAYGON_VIDEO_FACTORY.mp4"):
        if marker not in html:
            raise RuntimeError(f"UI v6 incompleta: {marker}")
    if "cuts_per_minute > 3.0" not in continuity:
        raise RuntimeError("Politica long-form conservadora v6 ausente")


def main() -> int:
    print("=" * 76)
    print("DRSAYGON VIDEO FACTORY 6.0.0 - RELEASE GATE")
    print("=" * 76)
    try:
        static_gate()
        print("[OK] Gate estatico: identidade, audio, continuidade, exportacao e UI v6.")
    except Exception as exc:
        return fail(f"Gate estatico falhou: {exc}")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    for index, test in enumerate(TESTS, start=1):
        path = ROOT / "scripts" / test
        print(f"[{index}/{len(TESTS)}] {test}")
        proc = subprocess.run(
            [sys.executable, str(path)], cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.returncode != 0:
            return fail(f"{test} falhou com codigo {proc.returncode}")

    print("=" * 76)
    print("[OK] VIDEO FACTORY 6.0.0 APROVADO PELO RELEASE GATE COMPLETO.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
