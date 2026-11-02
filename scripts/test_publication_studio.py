from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models import EditPlan, PublicationContext, PublicationPackage, ProjectCreate
from app.services.publication import normalize_publication_package


def make_plan(title: str, narration: str, seconds: int = 60) -> EditPlan:
    return EditPlan.model_validate({
        "project_title": title,
        "objective": "Publicacao generica",
        "target_duration_seconds": seconds,
        "blocks": [{
            "number": 1,
            "title": title,
            "narration": narration,
            "estimated_seconds": min(30, seconds),
            "clips": [{"filename": "teste.mp4", "start_seconds": 0, "end_seconds": 5, "reason": "demo"}],
        }],
    })


def main():
    # Regression 1: exact 100-char Short title without duplicated brand/model tail.
    plan = make_plan(
        "Banding na câmera",
        "Mostro a comutação do nobreak, a faixa escura na câmera e explico o efeito de banding e rolling shutter.",
    )
    package = PublicationPackage(
        recommended_title="Teste de comutação e faixa escura na câmera com Nobreak Intelbras XNB 720: XNB 720 Intelbras #shorts",
        alternative_titles=[
            "Intelbras XNB 720: faixa escura na câmera durante a comutação para bateria #shorts",
            "Nobreak Intelbras XNB 720: entenda banding e rolling shutter na comutação #shorts",
        ],
        description="Descrição suficientemente longa para validar o Publication Studio genérico.",
        pinned_comment="Você já viu esse efeito?",
        tags=[
            "Nobreak Intelbras XNB 720", "banding", "rolling shutter", "faixa escura câmera", "comutação nobreak",
            *[f"termo busca {i}" for i in range(1, 31)],
        ],
    )
    ctx = PublicationContext(
        content_type="product_review",
        primary_subject="Nobreak Intelbras XNB 720",
        product_name="Nobreak Intelbras XNB 720",
        is_product_specific=True,
        factual_research_relevant=True,
        research_mode="product_official",
        technical_specs_relevant=True,
        search_intents=["nobreak intelbras", "xnb 720", "banding câmera", "rolling shutter", "comutação bateria"],
    )
    project = {"format": "short_9_16", "product": "Nobreak Intelbras XNB 720", "name": "Short XNB 720"}
    out = normalize_publication_package(package, project, plan, [], "unavailable", context=ctx)
    assert len(out.recommended_title) == 100
    assert out.recommended_title.endswith("#shorts")
    low = out.recommended_title.casefold()
    assert low.count("intelbras") == 1, out.recommended_title
    assert low.count("xnb") == 1, out.recommended_title
    assert low.count("720") == 1, out.recommended_title
    assert len(out.tags) == 25
    assert len({x.casefold() for x in out.tags}) == 25
    assert out.version == "6.0.0"

    # Regression 2: no product is required; kids/family metadata does not get
    # polluted by product-review fallback vocabulary.
    ProjectCreate(
        name="Passeio da Yasmin",
        product="",
        objective="Vídeo infantil de passeio em família",
        format="youtube_16_9",
        desired_minutes=8,
    )
    kids_plan = make_plan(
        "Passeio em família",
        "Hoje a Yasmin vai passear, brincar e mostrar os momentos mais divertidos do dia no parque.",
        480,
    )
    kids_package = PublicationPackage(
        recommended_title="Passeio divertido em família no parque com brincadeiras, aventuras e momentos especiais",
        alternative_titles=["Dia de passeio no parque com brincadeiras e muita diversão em família"],
        description="Acompanhe os momentos do passeio em família e as brincadeiras ao longo do dia.",
        pinned_comment="Qual foi o momento mais divertido?",
        tags=["passeio em família", "brincadeiras", "parque", "vídeo infantil", "aventura em família", *[f"momento divertido {i}" for i in range(1, 30)]],
    )
    kids_ctx = PublicationContext(
        content_type="kids_family",
        primary_subject="Passeio em família no parque",
        research_mode="none",
        factual_research_relevant=False,
        technical_specs_relevant=False,
        search_intents=["passeio em família", "brincadeiras no parque", "vídeo infantil", "aventura em família"],
    )
    kids_out = normalize_publication_package(
        kids_package,
        {"format": "youtube_16_9", "product": "", "name": "Passeio da Yasmin"},
        kids_plan,
        [],
        "skipped",
        context=kids_ctx,
    )
    assert len(kids_out.recommended_title) == 100
    assert "review" not in kids_out.recommended_title.casefold()
    assert "unboxing" not in kids_out.recommended_title.casefold()
    assert "produto" not in kids_out.recommended_title.casefold()
    assert len(kids_out.tags) == 25
    assert kids_out.content_type == "kids_family"
    assert kids_out.research_mode == "none"

    print("Publication Studio v6.0.0: generico + titulo 100 sem redundancia + projeto sem produto + 25 tags = OK")


if __name__ == "__main__":
    main()
