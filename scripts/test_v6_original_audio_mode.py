from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import FFMPEG_BIN
from app.models import ClipReference, EditPlan, ProjectKnowledge, ProjectPaths, ProjectSettings, ScriptBlock

# This regression exercises the deterministic original-audio branch and does not
# need Gemini. Stub network-only modules so the release gate can run offline too.
import types
_fake_gemini = types.ModuleType("app.services.gemini")
for _name in ("align_narration_audio", "create_sync_matches", "review_rough_cut", "verify_visual_event"):
    setattr(_fake_gemini, _name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network path not expected")))
sys.modules["app.services.gemini"] = _fake_gemini
_fake_directed = types.ModuleType("app.services.directed_evidence")
_fake_directed.search_directed_evidence = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("directed search not expected"))
sys.modules["app.services.directed_evidence"] = _fake_directed
import app.services.professional_sync as ps


def make_av(path: Path, seconds: float = 8.0) -> None:
    subprocess.run([
        FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=10:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    ], check=True)


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="drsaygon_original_audio_"))
    try:
        root = temp / "project"
        paths = ProjectPaths(
            root=root, footage=root/"01_FILMAGENS", information=root/"02_INFORMACOES",
            analysis=root/"03_ANALISE", narration=root/"04_NARRACAO", result=root/"05_RESULTADO",
            publication=root/"06_PUBLICACAO", temp=root/"_TEMP", logs=root/"_LOGS",
        )
        for x in paths.model_dump().values(): Path(x).mkdir(parents=True, exist_ok=True)
        make_av(paths.footage / "cena.mp4")
        settings = ProjectSettings(
            name="Teste original", slug="teste", created_at="2026-01-01", model="offline",
            resolution="1920x1080", fps=30, audio_mode="original", desired_minutes=1,
        )
        plan = EditPlan(
            project_title="Teste", objective="preservar audio", target_duration_seconds=6,
            blocks=[ScriptBlock(
                number=1, title="Conversa", narration="", estimated_seconds=6,
                clips=[ClipReference(filename="cena.mp4", start_seconds=1.0, end_seconds=7.0, reason="fala original", audio_strategy="feature", shot_style="hold")],
            )],
        )
        catalog = {"analysis_mode":"autopilot_evidence_v5", "videos":[{
            "filename":"cena.mp4", "sequence_index":1, "duration_seconds":8.0,
            "segments":[{"event_id":"e1","start_seconds":1.0,"end_seconds":7.0,"description":"pessoa falando naturalmente","quality":"boa","use":True,"original_audio_is_important":True,"confidence":.95,"shot_style":"hold"}],
        }]}
        old = ps.ROUGH_CUT_QA_ENABLED
        ps.ROUGH_CUT_QA_ENABLED = False
        try:
            sync = ps._build_original_audio_sync(paths, plan, catalog, settings, ProjectKnowledge(), "", lambda _: None, lambda *_: None)
        finally:
            ps.ROUGH_CUT_QA_ENABLED = old
        if not sync.ready or len(sync.visual_runs) != 1:
            raise RuntimeError(sync.model_dump())
        run = sync.visual_runs[0]
        if run.audio_strategy != "feature":
            raise RuntimeError("Audio original nao foi preservado em destaque")
        if abs(run.source_start_seconds - 1.0) > .1 or abs(run.source_end_seconds - 7.0) > .1:
            raise RuntimeError(f"Faixa original foi alterada: {run.source_start_seconds}-{run.source_end_seconds}")
        if any((paths.narration).glob("*.wav")):
            raise RuntimeError("Modo original criou dependencia de narracao")
        print("[OK] Audio original v6: sem etapa de narracao, faixa e som da filmagem preservados.")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
