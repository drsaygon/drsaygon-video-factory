from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Unit test only: app.main imports the Gemini client, but this regression never
# performs network calls. Provide a minimal module stub when google-genai is not
# installed in the release-build environment. The user's real .venv still uses
# the actual dependency.
try:
    from google import genai as _real_genai  # type: ignore
except Exception:
    import types as _pytypes
    google_mod = sys.modules.setdefault("google", _pytypes.ModuleType("google"))
    genai_mod = _pytypes.ModuleType("google.genai")
    genai_types_mod = _pytypes.ModuleType("google.genai.types")
    class _DummyClient:
        pass
    genai_mod.Client = _DummyClient
    genai_mod.types = genai_types_mod
    setattr(google_mod, "genai", genai_mod)
    sys.modules["google.genai"] = genai_mod
    sys.modules["google.genai.types"] = genai_types_mod

from app.main import job_key, jobs, mirror_job_progress, set_job
from app.models import NarrationBeat
from app.services.professional_sync import (
    _candidate_map,
    _safe_context_candidate,
    _visual_obligation,
)


def beat(text: str) -> NarrationBeat:
    return NarrationBeat(
        beat_id="test_v01",
        block_number=1,
        beat_number=1,
        text=text,
        output_start_seconds=0.0,
        output_end_seconds=3.0,
        required_observation=text,
        visual_obligation=_visual_obligation(text),
        critical=_visual_obligation(text) == "hard",
    )


def main() -> int:
    # Regression: phrases that caused v6.0 to spend minutes searching and then
    # fail closed are explanatory/contextual, not hard visual obligations.
    expected = {
        "perceba que no exato momento em que eu retiro o nobreak da tomada": "hard",
        "aparece essa faixa escura correndo na imagem": "hard",
        "Repara de novo na comutação.": "contextual",
        "Quando muda a forma como essas luzes estão sendo alimentadas,": "abstract",
        "Isso acontece porque o sensor da câmera registra a imagem linha por linha": "abstract",
        "associada ao chamado rolling shutter": "contextual",
    }
    for text, obligation in expected.items():
        actual = _visual_obligation(text)
        assert actual == obligation, f"Obrigacao errada: {text!r} -> {actual}, esperado {obligation}"

    # Creator-confirmed evidence must outrank uncertain generic B-roll.
    target = beat("aparece essa faixa escura correndo na imagem")
    evidence = [
        {
            "event_id": "generic",
            "filename": "a.mp4",
            "description": "lâmpadas acesas",
            "actions": [], "objects": ["lâmpadas"], "states": ["acesas"],
            "visual_phenomena": [], "tags": [], "recommended_use": "b-roll",
            "confidence": 0.95, "critical_action": False, "verified_by_user": False,
            "detection_source": "gemini_general", "shot_style": "hold",
        },
        {
            "event_id": "manual_banding",
            "filename": "close.mp4",
            "description": "faixa escura / banding visível",
            "actions": [], "objects": ["lâmpadas"], "states": ["bateria"],
            "visual_phenomena": ["faixa escura", "banding"], "tags": ["evidência confirmada"],
            "recommended_use": "mostrar banding", "confidence": 1.0,
            "critical_action": True, "verified_by_user": True,
            "detection_source": "manual", "shot_style": "demo",
        },
    ]
    ranked = _candidate_map(None, target, evidence)
    assert ranked and ranked[0].event_id == "manual_banding", "Evidência manual não recebeu prioridade"

    # Contextual narration must always have a safe visual fallback instead of
    # forcing an expensive directed search / fail closed.
    context = beat("Quando muda a forma como essas luzes estão sendo alimentadas")
    context.visual_obligation = "abstract"
    context.critical = False
    by_id = {item["event_id"]: item for item in evidence}
    fallback = _safe_context_candidate(context, evidence, by_id, "manual_banding", ["close.mp4"])
    assert fallback and fallback.event_id == "manual_banding"

    # Regression for the UI bug reported at 62%: child progress must move the
    # Autopilot parent monotonically through its assigned stage range.
    slug = "__autopilot_reliability_test__"
    jobs.pop(job_key(slug, "autopilot"), None)
    jobs.pop(job_key(slug, "sync"), None)
    set_job(slug, "autopilot", status="running", progress=60, message="Teste")
    with mirror_job_progress(slug, "sync", "autopilot", 60, 84, "Sincronismo e continuidade"):
        set_job(slug, "sync", status="running", progress=25, message="Verificando 1/4")
        p1 = jobs[job_key(slug, "autopilot")].progress
        set_job(slug, "sync", status="running", progress=75, message="Verificando 3/4")
        p2 = jobs[job_key(slug, "autopilot")].progress
        assert 60 < p1 < p2 < 84, (p1, p2)
        assert "Verificando 3/4" in jobs[job_key(slug, "autopilot")].message
    jobs.pop(job_key(slug, "autopilot"), None)
    jobs.pop(job_key(slug, "sync"), None)

    print("[OK] Autopilot Reliability v6: gating contextual, prioridade autor e progresso espelhado aprovados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
