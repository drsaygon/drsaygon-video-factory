from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models import (
    PublicationContext, PublicationDescriptionBlueprint, PublicationDescriptionSection,
    PublicationFactSheet, PublicationSpecFact,
)
from app.services.description_composer import compose_description


def main():
    product_ctx = PublicationContext(
        content_type="product_review", primary_subject="Produto XPTO 720", product_name="Produto XPTO 720",
        is_product_specific=True, factual_research_relevant=True, research_mode="product_official",
        technical_specs_relevant=True,
    )
    sheet = PublicationFactSheet(
        exact_subject_match=True,
        subject="Produto XPTO 720",
        specifications=[
            PublicationSpecFact(label="Potência", value="720 VA"),
            PublicationSpecFact(label="Bateria", value="12 V / 7 Ah"),
            PublicationSpecFact(label="Autonomia", value="até 25 minutos em cenário informado pelo fabricante"),
            PublicationSpecFact(label="Quantidade de tomadas", value="4 tomadas"),
            PublicationSpecFact(label="Peso", value="4,1 kg"),
            PublicationSpecFact(label="Dimensões", value="35 x 21,3 x 14,2 cm"),
            PublicationSpecFact(label="Tensão de entrada", value="120 V"),
            PublicationSpecFact(label="Tensão de saída", value="120 V"),
        ],
        features=["DC Start", "Restart automático", "Carregamento automático da bateria"],
        package_contents=["1 nobreak", "1 manual do usuário"],
    )
    blueprint = PublicationDescriptionBlueprint(
        opening_paragraphs=[
            "Neste vídeo demonstro de forma prática o comportamento do equipamento durante a comutação.",
            "A gravação mostra o fenômeno visual e explica o que o espectador está vendo.",
        ],
        sections=[PublicationDescriptionSection(title="Conteúdo futuro", paragraphs=["Em breve haverá um review completo com novos testes."])],
        closing_paragraphs=["Confira também os próximos vídeos do canal."],
    )
    text = compose_description(blueprint, product_ctx, sheet)
    for required in [
        "720 VA", "12 V / 7 Ah", "4 tomadas", "4,1 kg", "35 x 21,3 x 14,2 cm",
        "DC Start", "Conteúdo da embalagem", "1 nobreak", "Conteúdo futuro",
        "O modelo dispõe de 720 VA e 4 tomadas.", "tensão de entrada 120 V", "tensão de saída 120 V",
    ]:
        assert required in text, required
    assert "Características técnicas e recursos confirmados:" in text

    kids_ctx = PublicationContext(content_type="kids_family", primary_subject="Passeio em família", research_mode="none")
    kids_blueprint = PublicationDescriptionBlueprint(
        opening_paragraphs=["Neste vídeo acompanhamos um passeio divertido em família."],
        sections=[PublicationDescriptionSection(title="Momentos do vídeo", bullets=["brincadeiras", "passeio", "momento especial"])],
        closing_paragraphs=["Acompanhe os próximos passeios."],
    )
    kids = compose_description(kids_blueprint, kids_ctx, PublicationFactSheet())
    assert "Características técnicas" not in kids
    assert "Conteúdo da embalagem" not in kids
    assert "Momentos do vídeo" in kids
    print("ADAPTIVE_DESCRIPTION_COMPOSER_V6_OK")


if __name__ == "__main__":
    main()
