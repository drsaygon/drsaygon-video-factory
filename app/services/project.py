from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.config import GEMINI_MODEL, WORKSPACE
from app.models import ProjectCreate, ProjectKnowledge, ProjectPaths, ProjectSettings, ProjectSummary


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def slugify(value: str) -> str:
    value = value.lower().strip()
    replacements = str.maketrans("áàâãéêíóôõúç", "aaaaeeiooouc")
    value = value.translate(replacements)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:72] or "projeto"


def project_root(slug: str) -> Path:
    return WORKSPACE / slug


def paths_for(slug: str) -> ProjectPaths:
    root = project_root(slug)
    return ProjectPaths(
        root=root,
        footage=root / "01_FILMAGENS",
        information=root / "02_INFORMACOES",
        analysis=root / "03_ANALISE",
        narration=root / "04_NARRACAO",
        result=root / "05_RESULTADO",
        publication=root / "06_PUBLICACAO",
        temp=root / "_TEMP",
        logs=root / "_LOGS",
    )




def knowledge_path(slug: str) -> Path:
    return project_root(slug) / "conhecimento_projeto.json"


def _existing_footage_names(slug: str) -> list[str]:
    paths = paths_for(slug)
    if not paths.footage.exists():
        return []
    return [p.name for p in sorted(paths.footage.iterdir(), key=lambda x: x.name.casefold()) if p.is_file()]


def load_knowledge(slug: str) -> ProjectKnowledge:
    data = read_json(knowledge_path(slug))
    # Existing v4 projects had no chronology_source. Preserve their saved order
    # as manual rather than silently reordering a project during an upgrade.
    if data and "chronology_source" not in data:
        data["chronology_source"] = "manual"
    knowledge = ProjectKnowledge.model_validate(data) if data else ProjectKnowledge()
    existing = _existing_footage_names(slug)
    existing_set = set(existing)
    ordered = [name for name in knowledge.footage_order if name in existing_set]
    ordered.extend(name for name in existing if name not in ordered)
    knowledge.footage_order = ordered
    return knowledge


def save_knowledge(slug: str, knowledge: ProjectKnowledge) -> ProjectKnowledge:
    existing = _existing_footage_names(slug)
    existing_set = set(existing)
    ordered = [name for name in knowledge.footage_order if name in existing_set]
    ordered.extend(name for name in existing if name not in ordered)
    knowledge.footage_order = ordered
    knowledge.updated_at = now_iso()
    write_json(knowledge_path(slug), knowledge.model_dump(mode="json"))
    return knowledge


def ordered_footage(slug: str) -> list[Path]:
    paths = paths_for(slug)
    knowledge = load_knowledge(slug)
    by_name = {p.name: p for p in paths.footage.iterdir() if p.is_file()} if paths.footage.exists() else {}
    return [by_name[name] for name in knowledge.footage_order if name in by_name]


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def create_project(payload: ProjectCreate) -> ProjectSettings:
    base = slugify(payload.name)
    slug = base
    suffix = 2
    while project_root(slug).exists():
        slug = f"{base}-{suffix}"
        suffix += 1

    resolution = "1080x1920" if payload.format == "short_9_16" else "1920x1080"
    settings = ProjectSettings(
        **payload.model_dump(),
        slug=slug,
        created_at=now_iso(),
        model=GEMINI_MODEL,
        resolution=resolution,
        fps=30,
    )
    paths = paths_for(slug)
    for path in paths.model_dump().values():
        Path(path).mkdir(parents=True, exist_ok=True)
    (paths.analysis / "videos").mkdir(parents=True, exist_ok=True)
    (paths.narration / "ia").mkdir(parents=True, exist_ok=True)
    write_json(paths.root / "projeto.json", settings.model_dump(mode="json"))
    save_knowledge(slug, ProjectKnowledge())
    return settings


def load_settings(slug: str) -> ProjectSettings:
    data = read_json(project_root(slug) / "projeto.json")
    if not data:
        raise FileNotFoundError(f"Projeto nao encontrado: {slug}")
    # backwards-compatible migration from v1 projects
    if "format" not in data:
        data["format"] = "youtube_16_9"
    if "short_seconds" not in data:
        data["short_seconds"] = 60
    if data.get("resolution") == "1280x720":
        data["resolution"] = "1920x1080"
    # v6.0.1 export migration: the old "none" delivery profile meant no
    # dedicated publication file. Keep that intent while moving to two
    # independent, explicit output toggles.
    old_delivery = str(data.get("delivery_profile", "youtube_1080p"))
    if "create_delivery" not in data:
        data["create_delivery"] = old_delivery != "none"
    if old_delivery == "none":
        data["delivery_profile"] = "youtube_1080p"
    if "master_profile" not in data:
        data["master_profile"] = "premiere_hq"
    # Defensive recovery for hand-edited/legacy project files. Never load a
    # project into an impossible state with no output selected.
    if not bool(data.get("create_master", True)) and not bool(data.get("create_delivery", True)):
        data["create_delivery"] = True
    return ProjectSettings.model_validate(data)




def save_settings(slug: str, settings: ProjectSettings) -> ProjectSettings:
    settings.slug = slug
    write_json(project_root(slug) / "projeto.json", settings.model_dump(mode="json"))
    return settings


def archive_render_files(paths: ProjectPaths, stamp: str) -> dict[str, object]:
    """Move every current render artifact into HISTORICO using one shared stamp.

    Pure file operation used by the update/render invalidation path and by the
    release gate. It deliberately archives Master and publication output
    together so an old file can never remain beside a newly selected profile.
    """
    delivery = paths.result / "CONTEUDO_PRINCIPAL.mp4"
    master = paths.result / "MASTER_DRSAYGON_VIDEO_FACTORY.mp4"
    report = paths.result / "RELATORIO_DE_EDICAO.json"
    present = [path for path in (delivery, master) if path.exists()]
    if not present and not report.exists():
        return {"outputs": [], "report": ""}
    history = paths.result / "HISTORICO"
    history.mkdir(parents=True, exist_ok=True)
    archived_outputs: list[str] = []
    for source in present:
        archived = history / f"{source.stem}_{stamp}{source.suffix}"
        shutil.move(str(source), str(archived))
        archived_outputs.append(archived.name)
    archived_report = ""
    if report.exists():
        target = history / f"RELATORIO_DE_EDICAO_{stamp}.json"
        shutil.move(str(report), str(target))
        archived_report = target.name
    return {"outputs": archived_outputs, "report": archived_report}

def project_status(slug: str) -> str:
    paths = paths_for(slug)
    if (paths.result / "CONTEUDO_PRINCIPAL.mp4").exists() or (paths.result / "MASTER_DRSAYGON_VIDEO_FACTORY.mp4").exists():
        return "concluido"
    if (paths.analysis / "roteiro.json").exists():
        return "roteiro"
    if (paths.analysis / "catalogo_cenas.json").exists():
        return "analisado"
    return "novo"


def list_projects() -> list[ProjectSummary]:
    items: list[ProjectSummary] = []
    if not WORKSPACE.exists():
        return items
    for root in WORKSPACE.iterdir():
        if not root.is_dir() or not (root / "projeto.json").exists():
            continue
        try:
            settings = load_settings(root.name)
        except Exception:
            continue
        items.append(ProjectSummary(
            slug=settings.slug,
            name=settings.name,
            product=settings.product,
            created_at=settings.created_at,
            status=project_status(settings.slug),
            root=str(root),
            format=settings.format,
        ))
    return sorted(items, key=lambda item: item.created_at, reverse=True)


def open_folder(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])
