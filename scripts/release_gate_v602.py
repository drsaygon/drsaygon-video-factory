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

TESTS = [
    "test_backend_import_v602.py",
    "test_v6_manual_recording_edges.py",
    "test_v602_manual_recording_smart_trim.py",
    "test_v602_evidence_player.py",
    "test_v6_original_audio_mode.py",
    "test_v6_mixed_audio.py",
    "test_v6_scene_audio_overrides.py",
    "test_v6_longform_pacing.py",
    "test_quality_autorepair.py",
    "test_continuity_engine.py",
    "test_v601_export_profiles_metadata.py",
    "test_v601_export_command_contract.py",
    "test_v601_export_settings.py",
    "test_v601_export_ui_static.py",
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
    py_files = [p for base in (ROOT / "app", ROOT / "voice", ROOT / "scripts") for p in base.rglob("*.py")]
    for path in py_files:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    from app.config import APP_NAME, APP_VERSION
    if APP_NAME != "DrSaygon Video Factory" or APP_VERSION != "6.0.2":
        raise RuntimeError(f"Identidade inesperada: {APP_NAME} {APP_VERSION}")

    ffmpeg_code = (ROOT / "app/services/ffmpeg.py").read_text(encoding="utf-8")
    assembler_code = (ROOT / "app/services/assembler.py").read_text(encoding="utf-8")
    sync_code = (ROOT / "app/services/professional_sync.py").read_text(encoding="utf-8")
    project_code = (ROOT / "app/services/project.py").read_text(encoding="utf-8")
    main_code = (ROOT / "app/main.py").read_text(encoding="utf-8")
    models = (ROOT / "app/models.py").read_text(encoding="utf-8")
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    css = (ROOT / "app/static/styles.css").read_text(encoding="utf-8")
    continuity = (ROOT / "app/services/continuity.py").read_text(encoding="utf-8")

    required_ffmpeg = (
        "def convert_recording_to_wav",
        "def export_master",
        "premiere_max",
        "def export_delivery",
        "writing_application=DrSaygon Video Factory",
    )
    for marker in required_ffmpeg:
        if marker not in ffmpeg_code:
            raise RuntimeError(f"Recurso FFmpeg ausente: {marker}")
    manual_fn = ffmpeg_code[ffmpeg_code.index("def convert_recording_to_wav"):ffmpeg_code.index("def prepare_voice_reference")]
    if "silenceremove" in manual_fn:
        raise RuntimeError("Gravação manual ainda contém silenceremove")
    for marker in ("def safe_speech_bounds", "silencedetect", "pre_guard", "post_guard"):
        if marker not in ffmpeg_code:
            raise RuntimeError(f"Smart Edge Trim incompleto: {marker}")
    if '/footage-preview/' not in js or 'player.load()' not in js or 'manualEvidencePlayerStatus' not in html:
        raise RuntimeError("Evidence Lab ainda não usa player/proxy compatível")
    if '@app.get("/api/projects/{slug}/footage-preview/{filename}")' not in main_code:
        raise RuntimeError("Endpoint de preview compatível do Evidence Lab ausente")

    if "effective_audio = run.audio_strategy" not in assembler_code:
        raise RuntimeError("Assembler não respeita áudio por tomada")
    if "settings.create_delivery" not in assembler_code or "settings.master_profile" not in assembler_code:
        raise RuntimeError("Assembler não implementa as duas saídas independentes")
    if '"mute" if settings.audio_mode == "narration"' not in sync_code:
        raise RuntimeError("Modo narração não nasce com áudio original mutado")
    if "delivery_profile\"] = \"youtube_1080p\"" not in project_code or "create_delivery" not in project_code:
        raise RuntimeError("Migração v6.0.1 de saída não encontrada")
    if "archive_render_files" not in main_code or "archived_outputs" not in project_code or "MASTER_DRSAYGON_VIDEO_FACTORY.mp4" not in project_code:
        raise RuntimeError("Arquivamento transacional das duas saídas não encontrado")
    if "MasterProfile" not in models or "create_delivery" not in models:
        raise RuntimeError("Modelo de configuração de exportação v6.0.1 incompleto")
    if "FALE AGORA" not in js or "corte inteligente de silêncio" not in js:
        raise RuntimeError("UI de gravação manual com Smart Edge Trim ausente")
    for marker in ("createMaster", "masterProfile", "createDelivery", "deliveryProfile", "output-grid", "exportSummaryBadge"):
        if marker not in html and marker not in css:
            raise RuntimeError(f"Export Studio incompleto: {marker}")
    if 'value="none"' in html:
        raise RuntimeError("UI ainda expõe o antigo perfil ambíguo 'none'")
    if "Mantenha pelo menos uma saída ativa" not in js:
        raise RuntimeError("Guard amigável de pelo menos uma saída ausente")
    if ".output-grid" not in css or "grid-template-columns:repeat(2,minmax(0,1fr))" not in css:
        raise RuntimeError("Layout responsivo do Export Studio ausente")
    if "cuts_per_minute > 3.0" not in continuity:
        raise RuntimeError("Política long-form conservadora v6 ausente")


def main() -> int:
    print("=" * 78)
    print("DRSAYGON VIDEO FACTORY 6.0.2 - RELEASE GATE")
    print("=" * 78)
    try:
        static_gate()
        print("[OK] Gate estático: identidade, Smart Edge Trim, Evidence Player, continuidade, Export Studio, outputs e UI.")
    except Exception as exc:
        return fail(f"Gate estático falhou: {exc}")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    tests = list(TESTS)
    # Browser-real UI regression is a build/release-lab check, not a runtime
    # dependency. The installed updater must not require Playwright/Chromium.
    if os.getenv("DRSAYGON_FULL_UI_GATE", "").strip() == "1":
        tests.insert(12, "test_v601_export_ui_playwright.py")

    for index, test in enumerate(tests, start=1):
        path = ROOT / "scripts" / test
        print(f"[{index}/{len(tests)}] {test}")
        proc = subprocess.run(
            [sys.executable, str(path)], cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        if proc.stdout:
            print(proc.stdout.rstrip())
        if proc.returncode != 0:
            return fail(f"{test} falhou com código {proc.returncode}")

    print("=" * 78)
    print("[OK] VIDEO FACTORY 6.0.2 APROVADO PELO RELEASE GATE COMPLETO.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
