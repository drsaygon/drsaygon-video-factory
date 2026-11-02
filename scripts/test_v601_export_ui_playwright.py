from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="drsaygon_ui_v602_"))
    try:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        # Browser policy in the release environment blocks localhost/file URLs,
        # so render the real DOM/CSS/JS entirely in-memory with no navigation.
        html = html.replace('<link rel="stylesheet" href="/static/styles.css?v=6.0.2">', '')
        html = html.replace('<script src="/static/app.js?v=6.0.2-video-factory"></script>', '')
        css = (STATIC / "styles.css").read_text(encoding="utf-8")
        js = (STATIC / "app.js").read_text(encoding="utf-8")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path="/usr/bin/chromium", args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.set_content(html, wait_until="domcontentloaded")
            page.add_style_tag(content=css)
            page.evaluate("""
                window.fetch = async (url, options = {}) => {
                  const u = String(url);
                  let payload = {};
                  if (u.endsWith('/api/system')) payload = {version:'6.0.2', api_key_configured:true, ffmpeg:true, ffprobe:true, voice:{ok:true}};
                  else if (u.endsWith('/api/projects')) payload = [];
                  return new Response(JSON.stringify(payload), {status:200, headers:{'Content-Type':'application/json'}});
                };
            """)
            page.add_script_tag(content=js)
            page.evaluate("""
                state.currentSlug = 'ui-test';
                state.project = {
                  settings: {name:'Teste UI', format:'youtube_16_9', desired_minutes:7, fps:30, audio_mode:'narration', create_master:true, master_profile:'premiere_hq', create_delivery:true, delivery_profile:'youtube_1080p'},
                  footage: [], knowledge: {workflow_mode:'autopilot', editing_style:'auto', chronology_mode:'guided', footage_order:[]},
                  voice_blocks: [], sync: null, result_exists:false, master_exists:false, delivery_exists:false
                };
                document.querySelector('#welcomeView').classList.add('hidden');
                document.querySelector('#projectView').classList.remove('hidden');
                renderProjectSettings();
            """)
            page.locator('.tab[data-tab="assemble"]').click()
            page.locator("#tab-assemble").wait_for(state="visible")

            studio = page.locator(".export-studio").bounding_box()
            master_card = page.locator("#masterOutputCard").bounding_box()
            delivery_card = page.locator("#deliveryOutputCard").bounding_box()
            master_spec = page.locator("#masterSpec").bounding_box()
            assert studio and studio["width"] > 800, studio
            assert master_card and master_card["width"] > 380, master_card
            assert delivery_card and delivery_card["width"] > 380, delivery_card
            assert master_spec and master_spec["width"] > 300 and master_spec["height"] < 85, master_spec
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")

            assert page.locator("#createMaster").is_checked()
            assert page.locator("#createDelivery").is_checked()
            assert not page.locator("#masterProfile").is_disabled()
            assert not page.locator("#deliveryProfile").is_disabled()
            assert "Master + publicação" in page.locator("#assembleButton").inner_text()

            page.locator('label[title="Gerar Master para Premiere"]').click()
            assert not page.locator("#createMaster").is_checked()
            assert page.locator("#masterProfile").is_disabled()
            assert page.locator("#createDelivery").is_checked()
            assert not page.locator("#deliveryProfile").is_disabled()
            assert "arquivo de publicação" in page.locator("#assembleButton").inner_text()

            page.locator('label[title="Gerar Master para Premiere"]').click()
            page.locator('label[title="Gerar arquivo para publicação"]').click()
            assert page.locator("#createMaster").is_checked()
            assert not page.locator("#createDelivery").is_checked()
            assert not page.locator("#masterProfile").is_disabled()
            assert page.locator("#deliveryProfile").is_disabled()
            page.locator("#masterProfile").select_option("premiere_max")
            assert "80 Mb/s" in page.locator("#masterSpec").inner_text()
            assert "Master para Premiere" in page.locator("#assembleButton").inner_text()

            # Last-output guard: trying to disable the last output never leaves a broken state.
            page.locator('label[title="Gerar Master para Premiere"]').click()
            assert not page.locator("#createMaster").is_checked()
            assert page.locator("#createDelivery").is_checked()
            assert not page.locator("#deliveryProfile").is_disabled()

            # Responsive regression: no horizontal overflow and cards stack cleanly.
            page.set_viewport_size({"width": 1024, "height": 900})
            page.wait_for_timeout(100)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2")
            cards = page.locator(".output-card")
            first = cards.nth(0).bounding_box()
            second = cards.nth(1).bounding_box()
            assert first and first["width"] > 500, first
            assert second and second["width"] > 500, second
            browser.close()

        print("[OK] UI v6.0.2: layout amplo, toggles independentes, combos contextuais, guard e responsividade aprovados no Chromium.")
        return 0
    finally:
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
