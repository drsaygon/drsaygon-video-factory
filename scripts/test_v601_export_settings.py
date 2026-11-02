from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models import ProjectCreate
from app.services import project as project_service
from app.services.project import archive_render_files


def write_project(root: Path, slug: str, data: dict) -> None:
    folder = root / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "projeto.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def base_data(slug: str) -> dict:
    return {
        "name": "Projeto legado",
        "product": "",
        "objective": "Teste de migração",
        "format": "youtube_16_9",
        "desired_minutes": 7,
        "short_seconds": 60,
        "audio_mode": "narration",
        "create_master": True,
        "delivery_profile": "youtube_1080p",
        "slug": slug,
        "created_at": "2026-08-23T10:00:00-03:00",
        "model": "gemini-test",
        "resolution": "1920x1080",
        "fps": 30,
    }


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="drsaygon_v602_settings_"))
    original_workspace = project_service.WORKSPACE
    project_service.WORKSPACE = temp
    try:
        legacy_none = base_data("legacy-none")
        legacy_none["delivery_profile"] = "none"
        write_project(temp, "legacy-none", legacy_none)
        migrated = project_service.load_settings("legacy-none")
        assert migrated.create_master is True
        assert migrated.master_profile == "premiere_hq"
        assert migrated.create_delivery is False
        assert migrated.delivery_profile == "youtube_1080p"

        legacy_youtube = base_data("legacy-youtube")
        write_project(temp, "legacy-youtube", legacy_youtube)
        migrated2 = project_service.load_settings("legacy-youtube")
        assert migrated2.create_delivery is True
        assert migrated2.delivery_profile == "youtube_1080p"

        try:
            ProjectCreate(name="Inválido", create_master=False, create_delivery=False)
        except ValidationError:
            pass
        else:
            raise AssertionError("Modelo aceitou projeto sem nenhuma saída")

        # Regression: changing output settings must archive BOTH current outputs,
        # not only CONTEUDO_PRINCIPAL as v6.0.0 did.
        slug = "archive-both"
        data = base_data(slug)
        data.update({"master_profile": "premiere_hq", "create_delivery": True})
        write_project(temp, slug, data)
        paths = project_service.paths_for(slug)
        for folder in paths.model_dump().values():
            Path(folder).mkdir(parents=True, exist_ok=True)
        (paths.result / "CONTEUDO_PRINCIPAL.mp4").write_bytes(b"delivery")
        (paths.result / "MASTER_DRSAYGON_VIDEO_FACTORY.mp4").write_bytes(b"master")
        (paths.result / "RELATORIO_DE_EDICAO.json").write_text("{}", encoding="utf-8")
        archive_render_files(paths, "TESTE")
        assert not (paths.result / "CONTEUDO_PRINCIPAL.mp4").exists()
        assert not (paths.result / "MASTER_DRSAYGON_VIDEO_FACTORY.mp4").exists()
        history = paths.result / "HISTORICO"
        names = {p.name for p in history.iterdir()}
        assert any(name.startswith("CONTEUDO_PRINCIPAL_") and name.endswith(".mp4") for name in names)
        assert any(name.startswith("MASTER_DRSAYGON_VIDEO_FACTORY_") and name.endswith(".mp4") for name in names)
        assert any(name.startswith("RELATORIO_DE_EDICAO_") and name.endswith(".json") for name in names)

        print("[OK] Export Studio v6.0.2: migração legada, validação de saídas e arquivamento duplo aprovados.")
        return 0
    finally:
        project_service.WORKSPACE = original_workspace
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
