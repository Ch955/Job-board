"""Regenerate docs/icons/*.png from shapes below. Dev-only: needs `pip install playwright`.

    python scripts/make_icons.py
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

ICONS = Path(__file__).resolve().parent.parent / "docs" / "icons"

RADAR = """
  <g fill="none" stroke="{fg}" stroke-width="18">
    <circle cx="256" cy="256" r="168"/>
    <circle cx="256" cy="256" r="108" opacity=".7"/>
    <circle cx="256" cy="256" r="48" opacity=".5"/>
  </g>
  <path d="M256 256 L256 88 A168 168 0 0 1 401.5 172 Z" fill="{fg}" opacity=".35"/>
  <line x1="256" y1="256" x2="401.5" y2="172" stroke="{fg}" stroke-width="14" stroke-linecap="round"/>
  <circle cx="338" cy="150" r="20" fill="{dot}"/>
"""


def svg(bg: str | None, rx: int, scale: float, fg="#22c55e", dot="#bbf7d0") -> str:
    rect = f'<rect width="512" height="512" rx="{rx}" fill="{bg}"/>' if bg else ""
    t = 256 * (1 - scale)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">{rect}'
            f'<g transform="translate({t} {t}) scale({scale})">{RADAR.format(fg=fg, dot=dot)}</g></svg>')


TARGETS = {
    "icon-192.png": (192, svg("#0b1220", 112, 1)),
    "icon-512.png": (512, svg("#0b1220", 112, 1)),
    "maskable-512.png": (512, svg("#0b1220", 0, 0.78)),      # content inside the 80% safe zone
    "apple-touch-icon.png": (180, svg("#0b1220", 0, 0.9)),   # iOS rounds corners itself
    "badge-96.png": (96, svg(None, 0, 1.1, fg="#ffffff", dot="#ffffff")),  # Android status bar: alpha only
}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    for name, (size, markup) in TARGETS.items():
        page.set_viewport_size({"width": size, "height": size})
        page.set_content(f'<style>html,body{{margin:0;background:transparent}}svg{{width:{size}px;height:{size}px;display:block}}</style>{markup}')
        page.screenshot(path=str(ICONS / name), omit_background=True)
        print("wrote", name)
    browser.close()
