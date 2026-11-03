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
    "test_backend_import_v600.py",
    "test_backend_import_v602.py",
    "test_project_duration.py",
    "test_continuity_engine.py",
    "test_quality_autorepair.py",
    "test_autopilot_reliability.py",
    "test_adaptive_description_composer.py",
    "test_generic_publication_engine.py",
    "test_publication_studio.py",
    "test_v6_export_profiles_metadata.py",
    "test_v6_longform_pacing.py",
    "test_v6_manual_recording_edges.py",
    "test_v6_mixed_audio.py",
    "test_v6_original_audio_mode.py",
    "test_v6_scene_audio_overrides.py",
    "test_v601_export_command_contract.py",
    "test_v601_export_profiles_metadata.py",
    "test_v601_export_settings.py",
    "test_v601_export_ui_static.py",
    "test_v602_evidence_player.py",
    "test_v602_manual_recording_smart_trim.py",
    "test_v700_apex_intelligence.py",
]


def fail(message: str) -> int:
    print(f"[ERRO] {message}")
    return 1


def static_gate() -> None:
    py_files = [p for base in (ROOT / "app", ROOT / "voice", ROOT / "scripts") for p in base.rglob("*.py")]
    for path in py_files:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

    from app.config import APP_NAME, APP_VERSION
    if APP_NAME != "DrSaygon Video Factory" or APP_VERSION != "7.0.0":
        raise RuntimeError(f"Identidade inesperada: {APP_NAME} {APP_VERSION}")

    ffmpeg_code = (ROOT / "app/services/ffmpeg.py").read_text(encoding="utf-8")
    assembler_code = (ROOT / "app/services/assembler.py").read_text(encoding="utf-8")
    sync_code = (ROOT / "app/services/professional_sync.py").read_text(encoding="utf-8")
    voice_code = (ROOT / "app/services/voice_profile.py").read_text(encoding="utf-8")
    draft_qa_code = (ROOT / "app/services/draft_qa.py").read_text(encoding="utf-8")
    continuity_code = (ROOT / "app/services/continuity.py").read_text(encoding="utf-8")
    pub_code = (ROOT / "app/services/description_composer.py").read_text(encoding="utf-8")
    models = (ROOT / "app/models.py").read_text(encoding="utf-8")
    js = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    css = (ROOT / "app/static/styles.css").read_text(encoding="utf-8")

    # Phase 1: Models & Config
    required_models = (
        "PhysicalCutPoint", "AudioSilenceWindow", "PhysicalMediaMap", "VisualAnchor",
        "PhoneticBudget", "SyncStrategy", "RetentionHookType", "VisualComplexity",
    )
    for model in required_models:
        if model not in models:
            raise RuntimeError(f"Modelo v7.0 ausente em app/models.py: {model}")

    # Phase 2: Physical Media Extraction
    for fn in ("def extract_physical_media_map", "def apply_snap_to_cut", "safe_cut_timestamps"):
        if fn not in ffmpeg_code:
            raise RuntimeError(f"Motor físico FFmpeg v7.0 ausente: {fn}")

    # Phase 3: Phonetic Budget
    for fn in ("def calculate_phonetic_budget", "def estimate_spoken_duration_sec"):
        if fn not in voice_code:
            raise RuntimeError(f"Cálculo de orçamento fonético v7.0 ausente: {fn}")

    # Phase 4: Elastic Sync & Ducking
    for fn in ("def resolve_elastic_sync", "def calculate_sync_precision_score"):
        if fn not in sync_code:
            raise RuntimeError(f"Motor de sincronismo elástico v7.0 ausente: {fn}")

    # Phase 5: Pacing Audit & Surgical Repair
    if "def audit_retention_pacing" not in continuity_code:
        raise RuntimeError("Auditoria de ritmo de retenção v7.0 ausente em continuity.py")
    if "def audit_and_repair_scene_sync" not in draft_qa_code:
        raise RuntimeError("Auto-reparo cirúrgico por cena v7.0 ausente em draft_qa.py")

    # Phase 7: YouTube Chapters & UI Badges
    if "def compose_youtube_chapters" not in pub_code:
        raise RuntimeError("Gerador de capítulos cravados 00:00 ausente em description_composer.py")
    if "sync-score-badge" not in js or "sync-score-badge" not in css:
        raise RuntimeError("Badge de telemetria de sync score ausente na UI")


def main() -> int:
    print("=" * 78)
    print("DRSAYGON VIDEO FACTORY 7.0.0 - APEX AUTONOMOUS RELEASE GATE")
    print("=" * 78)
    try:
        static_gate()
        print("[OK] Gate estático: modelos v7, sinais físicos, orçamento fonético, sincronismo elástico e UI.")
    except Exception as exc:
        return fail(f"Gate estático falhou: {exc}")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)

    for index, script_name in enumerate(TESTS, start=1):
        script_path = ROOT / "scripts" / script_name
        if not script_path.exists():
            return fail(f"Script de teste ausente: {script_name}")
        print(f"[{index}/{len(TESTS)}] {script_name}")
        proc = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr)
            return fail(f"{script_name} falhou com código {proc.returncode}")
        first_line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if first_line:
            print(f"     {first_line}")

    print("=" * 78)
    print("[OK] DRSAYGON VIDEO FACTORY 7.0.0 APROVADO PELO APEX RELEASE GATE COMPLETO!")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
