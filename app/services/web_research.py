from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DrSaygonVideoFactory/6.0.2"
RETAILER_MARKERS = {
    "amazon.", "mercadolivre.", "mercadolibre.", "shopee.", "magazineluiza.",
    "casasbahia.", "kabum.", "aliexpress.", "ebay.", "walmart.", "carrefour.",
    "buscape.", "zoom.com", "madeiramadeira.", "extra.com", "pichau.",
}
GENERIC_TOKENS = {
    "nobreak", "no-break", "ups", "notebook", "laptop", "smartphone", "celular",
    "monitor", "camera", "câmera", "produto", "conjunto", "panela", "panelas",
    "review", "teste", "testes", "video", "vídeo", "short", "shorts", "modelo",
    "para", "com", "sem", "preto", "branco", "cinza", "novo", "nova", "interativo",
}


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "svg", "noscript"}:
            self.skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "svg", "noscript"} and self.skip:
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip:
            value = re.sub(r"\s+", " ", data).strip()
            if value:
                self.parts.append(value)


def _fetch(url: str, timeout: float = 12.0, max_bytes: int = 1_800_000) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_type = str(resp.headers.get("Content-Type", ""))
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        data = data[:max_bytes]
    return data, content_type


def _subject_tokens(subject: str) -> list[str]:
    tokens = [t.casefold() for t in re.findall(r"[\wÀ-ÿ-]+", subject, flags=re.UNICODE)]
    # Preserve model identifiers and numbers (e.g. XNB 720, RTX 4050, S24).
    # Removing them made a generic family/category page look as relevant as the
    # exact product page.
    return [t for t in tokens if t not in GENERIC_TOKENS and len(t) >= 2]


def _distinctive_model_tokens(subject: str) -> set[str]:
    tokens = set(_subject_tokens(subject))
    return {t for t in tokens if any(ch.isdigit() for ch in t) or (len(t) <= 6 and t.isalnum())}


def _search_query(subject: str, mode: str, custom_query: str = "") -> str:
    if custom_query.strip():
        return custom_query.strip()
    if mode == "product_official":
        return f'"{subject}" oficial fabricante manual ficha técnica especificações dimensões peso conteúdo da embalagem'
    if mode == "subject_official":
        return f'"{subject}" oficial informações evento local site organização'
    return f'"{subject}" oficial'


def _candidate_score(subject: str, title: str, url: str) -> float:
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    if not host or any(marker in host for marker in RETAILER_MARKERS):
        return -100.0
    tokens = _subject_tokens(subject)
    model_tokens = _distinctive_model_tokens(subject)
    score = 0.0
    title_low = title.casefold()
    url_low = url.casefold()
    for token in tokens:
        plain = token.replace("-", "")
        if token in host or plain in host.replace("-", ""):
            score += 8.0
        if token in title_low:
            score += 1.5
        if token in url_low:
            score += 1.0
        if token in model_tokens and (token in title_low or token in url_low):
            score += 5.0
    low_title = title.casefold()
    if any(k in low_title for k in ("oficial", "official", "manual", "ficha técnica", "ficha tecnica", "suporte", "support")):
        score += 3.0
    if any(bad in host for bad in ("wikipedia.", "youtube.", "facebook.", "instagram.", "tiktok.")):
        score -= 8.0
    return score


def _bing_rss(subject: str, mode: str = "product_official", custom_query: str = "") -> list[dict[str, str]]:
    query = _search_query(subject, mode, custom_query)
    url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote(query)
    data, _ = _fetch(url, timeout=10, max_bytes=700_000)
    root = ET.fromstring(data)
    results: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        desc = html.unescape(re.sub(r"<[^>]+>", " ", item.findtext("description") or ""))
        if link.startswith(("http://", "https://")):
            results.append({"title": title, "url": link, "snippet": re.sub(r"\s+", " ", desc).strip()})
    return results


def _ddg_html(subject: str, mode: str = "product_official", custom_query: str = "") -> list[dict[str, str]]:
    # Best-effort zero-cost fallback. The Bing RSS path is preferred because it
    # is structured; this parser is intentionally conservative.
    query = _search_query(subject, mode, custom_query)
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    data, _ = _fetch(url, timeout=10, max_bytes=800_000)
    text = data.decode("utf-8", errors="ignore")
    results: list[dict[str, str]] = []
    pattern = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
    for href, raw_title in pattern.findall(text):
        title = html.unescape(re.sub(r"<[^>]+>", " ", raw_title))
        href = html.unescape(href)
        if href.startswith("//"):
            href = "https:" + href
        parsed = urllib.parse.urlparse(href)
        q = urllib.parse.parse_qs(parsed.query)
        actual = q.get("uddg", [href])[0]
        if actual.startswith(("http://", "https://")):
            results.append({"title": re.sub(r"\s+", " ", title).strip(), "url": actual, "snippet": ""})
    return results


def _structured_product_text(data: bytes, content_type: str) -> str:
    """Best-effort extraction of JSON-LD/meta product facts from HTML.

    Manufacturer pages often expose weight, SKU/GTIN, dimensions or model data
    in structured markup even when the visible page is JavaScript-heavy. The
    extracted text is passed to the factual model as evidence, never trusted as
    a finished description by itself.
    """
    if "html" not in content_type.casefold() and b"<html" not in data[:5000].lower():
        return ""
    raw = data.decode("utf-8", errors="ignore")
    chunks: list[str] = []
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', raw, flags=re.I | re.S):
        payload = html.unescape(match.group(1)).strip()
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        queue = obj if isinstance(obj, list) else [obj]
        for item in queue:
            if isinstance(item, dict) and isinstance(item.get("@graph"), list):
                queue.extend(x for x in item["@graph"] if isinstance(x, dict))
            if not isinstance(item, dict):
                continue
            typ = str(item.get("@type", "")).casefold()
            if "product" not in typ:
                continue
            fields: list[str] = []
            for key in ("name", "model", "sku", "mpn", "gtin", "gtin8", "gtin12", "gtin13", "gtin14", "weight", "height", "width", "depth", "color", "description"):
                value = item.get(key)
                if value not in (None, "", [], {}):
                    fields.append(f"{key}: {value}")
            additional = item.get("additionalProperty")
            if isinstance(additional, list):
                for prop in additional[:80]:
                    if isinstance(prop, dict):
                        name = prop.get("name") or prop.get("propertyID")
                        value = prop.get("value")
                        if name and value not in (None, ""):
                            fields.append(f"{name}: {value}")
            if fields:
                chunks.append("DADOS ESTRUTURADOS DO PRODUTO\n" + "\n".join(fields))
    return "\n\n".join(chunks)[:12000]


def _visible_text(data: bytes, content_type: str) -> str:
    if "html" not in content_type.casefold() and b"<html" not in data[:5000].lower():
        return ""
    text = data.decode("utf-8", errors="ignore")
    parser = _VisibleTextParser()
    try:
        parser.feed(text)
    except Exception:
        pass
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _relevant_excerpt(text: str, subject: str, limit: int = 11000) -> str:
    if len(text) <= limit:
        return text
    tokens = [t for t in _subject_tokens(subject) if len(t) >= 3]
    low = text.casefold()
    positions: list[int] = []
    for token in tokens[:6]:
        start = 0
        while len(positions) < 12:
            pos = low.find(token, start)
            if pos < 0:
                break
            positions.append(pos)
            start = pos + len(token)
    if not positions:
        return text[:limit]
    chunks: list[str] = []
    used: list[tuple[int, int]] = []
    for pos in sorted(set(positions)):
        a = max(0, pos - 900)
        b = min(len(text), pos + 2200)
        if any(a < ub and b > ua for ua, ub in used):
            continue
        used.append((a, b))
        chunks.append(text[a:b])
        if sum(map(len, chunks)) >= limit:
            break
    return " … ".join(chunks)[:limit]


def collect_official_web_research(
    subject: str,
    logger=None,
    mode: str = "product_official",
    research_query: str = "",
) -> tuple[str, list[dict[str, str]]]:
    """Find/fetch likely first-party material without assuming every video is a product.

    product_official: exact-product page/manual/specifications are preferred and
    model identifiers must be present when the subject contains them.
    subject_official: official event/place/organization/person pages are preferred,
    without forcing technical-specification vocabulary.
    none: caller should normally skip research entirely.
    """
    log = logger or (lambda _msg: None)
    if mode == "none" or not subject.strip():
        return "", []

    candidates: list[dict[str, str]] = []
    errors: list[str] = []
    queries = [research_query.strip()] if research_query.strip() else []
    if mode == "product_official":
        queries.extend([
            f'"{subject}" oficial fabricante ficha técnica',
            f'"{subject}" manual especificações bateria potência dimensões peso',
            f'"{subject}" conteúdo da embalagem garantia conexões tomadas',
        ])
    elif mode == "subject_official":
        queries.extend([f'"{subject}" oficial', f'"{subject}" informações oficiais'])
    else:
        queries.append(f'"{subject}" oficial')
    seen_query: set[str] = set()
    queries = [q for q in queries if q and not (q.casefold() in seen_query or seen_query.add(q.casefold()))]

    for finder in (_bing_rss, _ddg_html):
        for query in queries[:4]:
            try:
                candidates.extend(finder(subject, mode=mode, custom_query=query))
            except Exception as error:
                errors.append(f"{finder.__name__}: {error}")
        if candidates:
            break
    if not candidates:
        if errors:
            log("Pesquisa web gratuita nao retornou resultados: " + " | ".join(errors))
        return "", []

    ranked = sorted(candidates, key=lambda x: _candidate_score(subject, x.get("title", ""), x.get("url", "")), reverse=True)
    sources: list[dict[str, str]] = []
    reports: list[str] = []
    seen_urls: set[str] = set()
    required_model_tokens = _distinctive_model_tokens(subject) if mode == "product_official" else set()
    subject_tokens = set(_subject_tokens(subject))

    for candidate in ranked:
        score = _candidate_score(subject, candidate.get("title", ""), candidate.get("url", ""))
        if score < 4.0:
            continue
        url = candidate["url"]
        if url in seen_urls:
            continue
        host = urlparse(url).netloc.casefold().removeprefix("www.")
        try:
            data, content_type = _fetch(url, timeout=12)
            text = _visible_text(data, content_type)
            structured = _structured_product_text(data, content_type) if mode == "product_official" else ""
            combined_text = (structured + "\n\n" + text).strip()
            if len(combined_text) < 250:
                continue
            low = combined_text.casefold()
            coverage = {token for token in subject_tokens if token in low or token in candidate.get("title", "").casefold() or token in url.casefold()}
            if subject_tokens and len(coverage) / max(1, len(subject_tokens)) < 0.45:
                continue
            if required_model_tokens:
                matched_models = {token for token in required_model_tokens if token in low or token in candidate.get("title", "").casefold() or token in url.casefold()}
                # Exact product pages must contain at least one distinctive model
                # identifier; this prevents a generic brand/category page from
                # becoming the source for model-specific specs.
                if not matched_models:
                    continue
            excerpt = _relevant_excerpt(combined_text, subject, limit=18000 if mode == "product_official" else 10000)
            sources.append({"title": candidate.get("title") or host, "url": url})
            label = "FONTE OFICIAL CANDIDATA" if mode == "product_official" else "FONTE PRIMARIA/OFICIAL CANDIDATA"
            reports.append(
                f"{label} {len(sources)}\nTitulo: {candidate.get('title') or host}\nURL: {url}\nConteudo extraido:\n{excerpt}"
            )
            seen_urls.add(url)
            log(f"Pesquisa oficial: fonte {len(sources)} carregada de {host} (score={score:.1f}, modo={mode}).")
            if len(sources) >= 4:
                break
        except Exception as error:
            log(f"Falha ao ler fonte candidata {host}: {error}")
    return "\n\n".join(reports), sources
