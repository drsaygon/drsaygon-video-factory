from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from app.models import PublicationContext, PublicationDescriptionBlueprint, PublicationFactSheet


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _dedupe(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = _clean(item)
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _spec_lines(sheet: PublicationFactSheet) -> list[str]:
    lines: list[str] = []
    for fact in sheet.specifications:
        label = _clean(fact.label).rstrip(":")
        value = _clean(fact.value)
        if label and value:
            lines.append(f"{label}: {value}")
    return _dedupe(lines)


def _feature_lines(sheet: PublicationFactSheet) -> list[str]:
    return _dedupe(sheet.features)


def _product_highlight_paragraphs(sheet: PublicationFactSheet) -> list[str]:
    """Create natural but deterministic prose from confirmed facts only."""
    def fold_label(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", _clean(value))
        ascii_like = "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()
        return re.sub(r"[^a-z0-9]+", " ", ascii_like).strip()

    facts = {fold_label(f.label): _clean(f.value) for f in sheet.specifications if _clean(f.value)}

    def find(*needles: str) -> str:
        for key, value in facts.items():
            if any(n in key for n in needles):
                return value
        return ""

    power = find("potencia", "capacidade")
    battery = find("bateria")
    autonomy = find("autonomia")
    outlets = find("quantidade de tomadas", "tomadas", "saidas", "conexoes")
    input_v = find("tensao de entrada")
    output_v = find("tensao de saida")
    wave = find("forma de onda")
    transfer = find("tempo de transferencia", "transferencia")

    paragraphs: list[str] = []
    sentences: list[str] = []
    if battery and autonomy:
        sentences.append(f"O produto utiliza {battery} e a autonomia informada é {autonomy}.")
    elif battery:
        sentences.append(f"A bateria confirmada para o modelo é {battery}.")
    elif autonomy:
        sentences.append(f"A autonomia informada para o modelo é {autonomy}.")
    if power and outlets:
        sentences.append(f"O modelo dispõe de {power} e {outlets}.")
    elif power:
        sentences.append(f"A capacidade/potência confirmada é {power}.")
    elif outlets:
        sentences.append(f"O modelo conta com {outlets}.")
    if sentences:
        paragraphs.append(" ".join(sentences))

    electrical: list[str] = []
    if input_v:
        electrical.append(f"tensão de entrada {input_v}")
    if output_v:
        electrical.append(f"tensão de saída {output_v}")
    if wave:
        electrical.append(f"forma de onda {wave}")
    if transfer:
        electrical.append(f"tempo de transferência {transfer}")
    if electrical:
        paragraphs.append("Entre os dados elétricos confirmados estão " + ", ".join(electrical) + ".")

    features = _feature_lines(sheet)
    if features:
        selected = features[:6]
        paragraphs.append("Entre os recursos confirmados para este modelo estão " + "; ".join(selected) + ".")
    return paragraphs


def compose_description(
    blueprint: PublicationDescriptionBlueprint,
    context: PublicationContext,
    sheet: PublicationFactSheet,
    max_chars: int = 5000,
) -> str:
    """Assemble the final description deterministically.

    AI writes editorial prose/sections, but model-specific technical data are
    injected from the verified fact sheet so they cannot be silently omitted.
    """
    parts: list[str] = []
    parts.extend(_dedupe(blueprint.opening_paragraphs))

    if context.is_product_specific and context.technical_specs_relevant and sheet.exact_subject_match:
        highlights = _product_highlight_paragraphs(sheet)
        if highlights:
            parts.append("\n".join(highlights))

        feature_lines = _feature_lines(sheet)
        spec_lines = _spec_lines(sheet)
        if feature_lines or spec_lines:
            block = ["Características técnicas e recursos confirmados:"]
            block.extend(f"- {item}" for item in feature_lines)
            block.extend(f"- {item}" for item in spec_lines)
            parts.append("\n".join(block))

        contents = _dedupe(sheet.package_contents)
        if contents:
            parts.append("Conteúdo da embalagem:\n" + "\n".join(f"- {item}" for item in contents))

    # Generic sections generated according to the content type. For product
    # videos, technical sections with confusing duplicate headings are skipped
    # because the deterministic composer already inserted verified data above.
    banned_product_titles = (
        "característica", "especifica", "conteúdo da embalagem", "conteudo da embalagem",
        "ficha técnica", "ficha tecnica",
    )
    for section in blueprint.sections:
        title = _clean(section.title)
        if context.is_product_specific and any(token in title.casefold() for token in banned_product_titles):
            continue
        body: list[str] = []
        body.extend(_dedupe(section.paragraphs))
        body.extend(f"- {item}" for item in _dedupe(section.bullets))
        if not body:
            continue
        parts.append((title + ":\n" if title else "") + "\n".join(body))

    parts.extend(_dedupe(blueprint.closing_paragraphs))
    text = "\n\n".join(p.strip() for p in parts if p.strip()).strip()
    if len(text) <= max_chars:
        return text

    # Preserve complete sections. Remove trailing optional sections before ever
    # clipping a technical fact in the middle of a line.
    kept: list[str] = []
    total = 0
    for part in parts:
        chunk = part.strip()
        extra = len(chunk) + (2 if kept else 0)
        if total + extra > max_chars:
            break
        kept.append(chunk)
        total += extra
    if kept:
        return "\n\n".join(kept)
    return text[:max_chars].rstrip()


def format_seconds_to_timestamp(seconds: float) -> str:
    """Format seconds into YouTube timestamp (MM:SS or HH:MM:SS)."""
    total_sec = max(0, int(round(seconds)))
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def compose_youtube_chapters(items: list[Any], default_intro: str = "Início") -> list[str]:
    """Generate YouTube chapters starting strictly at 00:00 using post-sync timings (Phase 7.1)."""
    if not items:
        return ["00:00 - Início"]
    chapters: list[str] = []
    first_title = ""
    for item in items:
        first_title = getattr(item, "title", None) or getattr(item, "reason", None)
        if isinstance(item, dict):
            first_title = item.get("title", item.get("reason", ""))
        if first_title:
            break
    chapters.append(f"00:00 - {first_title or default_intro}")

    for item in items:
        start_sec = 0.0
        if hasattr(item, "output_start_seconds"):
            start_sec = float(item.output_start_seconds)
        elif hasattr(item, "start_seconds"):
            start_sec = float(item.start_seconds)
        elif isinstance(item, dict):
            start_sec = float(item.get("output_start_seconds", item.get("start_seconds", 0.0)))

        title = getattr(item, "title", None) or getattr(item, "reason", None) or getattr(item, "evidence_description", "")
        if isinstance(item, dict):
            title = item.get("title", item.get("reason", item.get("evidence_description", "")))
        title = str(title).strip()

        if start_sec >= 10.0 and title:
            ts = format_seconds_to_timestamp(start_sec)
            line = f"{ts} - {title}"
            if line not in chapters:
                chapters.append(line)
    return chapters
