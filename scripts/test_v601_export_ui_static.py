from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def require(text: str, markers: tuple[str, ...], label: str) -> None:
    missing = [m for m in markers if m not in text]
    if missing:
        raise RuntimeError(f"{label}: marcadores ausentes: {missing}")


def main() -> int:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "styles.css").read_text(encoding="utf-8")

    require(html, (
        'id="createMaster"',
        'id="masterProfile"',
        'value="premiere_hq"',
        'value="premiere_max"',
        'id="createDelivery"',
        'id="deliveryProfile"',
        'value="youtube_1080p"',
        'value="social_1080p"',
        'id="exportSummaryBadge"',
        'id="exportSelectionTitle"',
        'id="exportSelectionText"',
        'id="assembleButton"',
    ), "HTML Export Studio")
    if 'value="none"' in html:
        raise RuntimeError("HTML ainda expõe o perfil legado ambíguo 'none'")

    require(js, (
        "function syncExportUI",
        "masterProfile.disabled = !master.checked",
        "deliveryProfile.disabled = !delivery.checked",
        "Mantenha pelo menos uma saída ativa",
        "2. Gerar Master + publicação",
        "2. Gerar Master para Premiere",
        "2. Gerar arquivo de publicação",
        "create_master: $('#createMaster').checked",
        "master_profile: $('#masterProfile').value",
        "create_delivery: $('#createDelivery').checked",
        "delivery_profile: $('#deliveryProfile').value",
    ), "JavaScript Export Studio")

    require(css, (
        ".export-studio",
        ".output-grid",
        "grid-template-columns:repeat(2,minmax(0,1fr))",
        ".output-card.enabled",
        ".switch-control",
        ".output-controls select:disabled",
        "@media(max-width:1050px)",
    ), "CSS Export Studio")

    print("[OK] UI estática v6.0.2: controles contextuais, guard de saída e layout responsivo presentes sem dependência de browser.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
