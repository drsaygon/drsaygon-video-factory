from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.models import EditPlan, PublicationContext, PublicationPackage, PublicationSource
from app.services.description_composer import compose_youtube_chapters, format_seconds_to_timestamp


_TITLE_STOP = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas",
    "um", "uma", "para", "por", "com", "sem", "que", "como", "ao", "aos", "the", "and", "of",
}


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()


def _title_tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[\wÀ-ÿ]+", _fold(text), flags=re.UNICODE) if len(t) >= 2]


def _meaningful_tokens(text: str) -> set[str]:
    return {t for t in _title_tokens(text) if t not in _TITLE_STOP}


def _strip_shorts(title: str) -> str:
    return re.sub(r"\s*#shorts\b", "", str(title or ""), flags=re.IGNORECASE).strip(" -|·:;,!?")


def normalize_short_title(title: str, is_short: bool) -> str:
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    suffix = " #shorts" if is_short else ""
    if is_short:
        title = _strip_shorts(title)
    max_base = 100 - len(suffix)
    if len(title) > max_base:
        clipped = title[: max_base + 1]
        if " " in clipped:
            clipped = clipped.rsplit(" ", 1)[0]
        title = clipped.rstrip(" -|·,:;!?")
    return (title + suffix).strip()[:100]


def _remove_redundant_tail(title: str) -> str:
    """Drop a trailing SEO fragment when it only repeats terms already present.

    Example rejected by the user:
    "... Nobreak Intelbras XNB 720: XNB 720 Intelbras"
    """
    value = re.sub(r"\s+", " ", title).strip()
    parts = re.split(r"\s+(?:\||—|–|-)\s+|:\s+", value)
    if len(parts) < 2:
        return value
    tail = parts[-1].strip()
    head = value[: value.rfind(tail)].rstrip(" -|—–:;")
    tail_tokens = _meaningful_tokens(tail)
    head_tokens = _meaningful_tokens(head)
    if tail_tokens and tail_tokens.issubset(head_tokens):
        return head
    if tail_tokens and len(tail_tokens & head_tokens) / max(1, len(tail_tokens)) >= 0.80:
        return head
    return value


def _strategic_terms(
    package: PublicationPackage,
    project_data: dict[str, Any],
    plan: EditPlan,
    context: PublicationContext,
) -> list[str]:
    """Content-agnostic SEO/search fragments ranked for title completion.

    No product assumption is made. The pool comes from the inferred content
    context, candidate tags, project subject and the actual narration.
    """
    values: list[str] = []
    values.extend(str(x).strip() for x in (context.search_intents or []) if str(x).strip())
    values.extend(str(x).strip() for x in (package.tags or []) if str(x).strip())
    subject = str(context.primary_subject or project_data.get("product") or project_data.get("name") or "").strip()
    if subject:
        values.append(subject)

    corpus = " ".join(
        f"{getattr(b, 'title', '')} {getattr(b, 'narration', '')}"
        for b in plan.blocks
    )
    words = [w for w in re.findall(r"[\wÀ-ÿ-]+", corpus, flags=re.UNICODE) if len(w) >= 3]
    # Actual phrases from the narration are safer than generic SEO filler and
    # work equally well for product reviews, kids videos, vlogs and tutorials.
    for n in (4, 3, 2, 1):
        for i in range(max(0, len(words) - n + 1)):
            values.append(" ".join(words[i:i+n]))
            if len(values) > 220:
                break
        if len(values) > 220:
            break

    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = re.sub(r"\s+", " ", raw).strip(" -|·,:;#!?")
        if len(value) < 3 or len(value) > 60:
            continue
        key = _fold(value)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _fragment_for_base(term: str, base: str) -> str:
    """Remove already-covered words from a candidate SEO fragment.

    This prevents additions such as "XNB 720 Intelbras" after a title that
    already contains "Intelbras XNB 720" while still allowing a genuinely new
    word such as "review" or "autonomia".
    """
    base_tokens = _meaningful_tokens(base)
    words = re.findall(r"[\wÀ-ÿ-]+", term, flags=re.UNICODE)
    kept: list[str] = []
    for word in words:
        folded = _fold(word)
        if folded in _TITLE_STOP:
            if kept:
                kept.append(word)
            continue
        if folded not in base_tokens:
            kept.append(word)
    cleaned = " ".join(kept).strip()
    return cleaned if _meaningful_tokens(cleaned) else ""


def _fill_title_base(base: str, max_base: int, terms: list[str]) -> tuple[str, int]:
    base = _remove_redundant_tail(re.sub(r"\s+", " ", base).strip().rstrip(" -|·,:;!?"))
    if len(base) > max_base:
        clipped = base[: max_base + 1]
        base = clipped.rsplit(" ", 1)[0] if " " in clipped else clipped[:max_base]
        base = base.rstrip(" -|·,:;!?")

    used: set[str] = set()
    while len(base) < max_base:
        remaining = max_base - len(base)
        choices: list[tuple[int, int, str, str]] = []
        for rank, term in enumerate(terms):
            useful = _fragment_for_base(term, base)
            if not useful:
                continue
            key = _fold(useful)
            if key in used:
                continue
            for sep in (" | ", " — ", ": ", " "):
                fragment = sep + useful
                if len(fragment) <= remaining:
                    # Prefer the best-ranked phrase, but use the available
                    # character budget efficiently.
                    choices.append((len(fragment), -rank, fragment, key))
        if not choices:
            break
        _, _, fragment, key = max(choices)
        base += fragment
        used.add(key)

    return base, max_base - len(base)


def _redundancy_penalty(title: str, subject: str) -> float:
    tokens = [t for t in _title_tokens(_strip_shorts(title)) if t not in _TITLE_STOP]
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    repeated = sum(max(0, n - 1) for n in counts.values())
    subject_tokens = _meaningful_tokens(subject)
    repeated_subject = sum(max(0, counts.get(t, 0) - 1) for t in subject_tokens)
    return repeated * 2.5 + repeated_subject * 8.0


def force_exact_title_100(
    title: str,
    is_short: bool,
    package: PublicationPackage,
    project_data: dict[str, Any],
    plan: EditPlan,
    context: PublicationContext,
) -> str:
    """Return exactly 100 chars while avoiding repeated subject/model terms."""
    suffix = " #shorts" if is_short else ""
    max_base = 100 - len(suffix)
    raw_base = _strip_shorts(title) if is_short else re.sub(r"\s+", " ", str(title or "")).strip()
    raw_base = _remove_redundant_tail(raw_base)
    terms = _strategic_terms(package, project_data, plan, context)

    variants: list[str] = []
    current = raw_base.rstrip(" -|·,:;!?")
    for _ in range(10):
        if current and current not in variants:
            variants.append(current)
        if " " not in current:
            break
        current = current.rsplit(" ", 1)[0].rstrip(" -|·,:;!?")

    subject = str(context.primary_subject or project_data.get("product") or project_data.get("name") or "")
    best: str | None = None
    best_score = -10**9
    for variant in variants or [raw_base]:
        filled, remaining = _fill_title_base(variant, max_base, terms)
        score = (
            len(_meaningful_tokens(filled)) * 3.0
            - _redundancy_penalty(filled, subject) * 10.0
            - remaining * 7.0
            + min(len(variant), max_base) * 0.10
        )
        if score > best_score:
            best_score = score
            best = filled

    base = (best or raw_base[:max_base]).rstrip()
    remaining = max_base - len(base)
    # Exact-width is a user requirement. We never repeat keywords merely to
    # fill the ceiling. If semantic phrases cannot consume the final 1-2 chars,
    # use natural punctuation; larger gaps use a neutral separator/punctuation
    # sequence rather than duplicated model/brand terms.
    if remaining > 0:
        tail = "?!" if remaining <= 2 else " —" + "!" * max(0, remaining - 2)
        base = (base + tail[:remaining])[:max_base]
    result = (base + suffix)[:100]
    if len(result) < 100:
        result += "!" * (100 - len(result))
    return result


def select_seo_title(
    package: PublicationPackage,
    project_data: dict[str, Any],
    plan: EditPlan,
    context: PublicationContext,
) -> str:
    is_short = project_data.get("format") == "short_9_16"
    subject = str(context.primary_subject or project_data.get("product") or project_data.get("name") or "")
    subject_tokens = _meaningful_tokens(subject)
    candidates = [package.recommended_title, *(package.alternative_titles or [])]
    normalized: list[str] = []
    for candidate in candidates:
        value = normalize_short_title(_remove_redundant_tail(candidate), is_short)
        if value and value not in normalized:
            normalized.append(value)
    if not normalized:
        normalized = [normalize_short_title(subject or "Vídeo", is_short)]

    def score(value: str) -> float:
        tokens = _meaningful_tokens(value)
        coverage = len(subject_tokens & tokens) / max(1, len(subject_tokens))
        length_score = 1.0 - min(abs(100 - len(value)), 60) / 60
        unique_ratio = len(tokens) / max(1, len(_title_tokens(value)))
        return coverage * 5.0 + length_score * 3.0 + unique_ratio * 2.0 - _redundancy_penalty(value, subject)

    selected = max(normalized, key=score)
    return force_exact_title_100(selected, is_short, package, project_data, plan, context)


def normalize_tags(
    tags: list[str],
    project_data: dict[str, Any],
    plan: EditPlan,
    context: PublicationContext | None = None,
) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        tag = re.sub(r"\s+", " ", str(raw)).strip().strip("#,.;")
        if not tag or len(tag) > 80:
            continue
        key = _fold(tag)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(tag)
        if len(cleaned) >= 25:
            return cleaned

    ctx = context or PublicationContext(primary_subject=str(project_data.get("product") or project_data.get("name") or ""))
    subject = str(ctx.primary_subject or project_data.get("product") or project_data.get("name") or "").strip()
    fallbacks: list[str] = [*ctx.search_intents]
    if subject:
        fallbacks.append(subject)

    stop = {
        "para","como","isso","essa","esse","uma","das","dos","com","sem","que","por","porque","quando","onde",
        "mais","muito","video","vídeo","sobre","entre","depois","antes","aqui","agora","voce","você","estou","esta","está",
    }
    corpus = " ".join(f"{getattr(b, 'title', '')} {getattr(b, 'narration', '')}" for b in plan.blocks)
    words = [w for w in re.findall(r"[\wÀ-ÿ]+", corpus, flags=re.UNICODE) if len(w) >= 3 and _fold(w) not in stop]
    for n in (4, 3, 2):
        for i in range(max(0, len(words) - n + 1)):
            fallbacks.append(" ".join(words[i:i+n]))
    fallbacks.extend(words)

    for item in fallbacks:
        tag = re.sub(r"\s+", " ", str(item)).strip().strip("#,.;")
        key = _fold(tag)
        if len(tag) < 2 or len(tag) > 80 or key in seen:
            continue
        seen.add(key)
        cleaned.append(tag)
        if len(cleaned) >= 25:
            break
    return cleaned[:25]


def normalize_publication_package(
    package: PublicationPackage,
    project_data: dict[str, Any],
    plan: EditPlan,
    sources: list[PublicationSource],
    research_status: str,
    context: PublicationContext | None = None,
) -> PublicationPackage:
    ctx = context or PublicationContext(primary_subject=str(project_data.get("product") or project_data.get("name") or ""))
    package.version = "6.0.0"
    package.content_type = ctx.content_type
    package.research_mode = ctx.research_mode
    package.recommended_title = select_seo_title(package, project_data, plan, ctx)
    package.alternative_titles = [
        force_exact_title_100(
            normalize_short_title(t, project_data.get("format") == "short_9_16"),
            project_data.get("format") == "short_9_16",
            package,
            project_data,
            plan,
            ctx,
        )
        for t in (package.alternative_titles or [])
        if str(t).strip()
    ][:6]
    package.tags = normalize_tags(package.tags, project_data, plan, ctx)
    package.research_status = research_status
    package.research_sources = sources
    return package
