#!/usr/bin/env python3
"""Re-render the Mermaid diagrams into docs/overview.html.

    python scripts/render_diagrams.py

Source of truth: the ```mermaid blocks in docs/DIAGRAMS.md, in order (GitHub
renders those natively). This script renders each to SVG via mermaid-cli
(needs node; puppeteer downloads a headless browser on first run), namespaces
the SVG ids so seven inline figures don't collide, strips fixed dimensions so
they scale responsively, and splices each into its matching
`<div class="diagram-panel" data-diagram="...">` in docs/overview.html.

Run it after editing DIAGRAMS.md, then re-publish/commit overview.html.
Adding a diagram = add the ```mermaid block AND a NAMES entry AND a tagged
<figure> in overview.html (the script refuses to guess placement).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIAGRAMS_MD = ROOT / "docs" / "DIAGRAMS.md"
OVERVIEW_HTML = ROOT / "docs" / "overview.html"

# One slug per ```mermaid block in DIAGRAMS.md, same order. Each must have a
# matching data-diagram panel in overview.html.
NAMES = ["e2e", "pipeline", "sequence", "identify", "checkpoint", "auth", "roster"]

# Matches the overview page's palette (light panel in both themes).
MERMAID_THEME = {
    "theme": "neutral",
    "themeVariables": {
        "fontFamily": "ui-monospace, SF Mono, Menlo, monospace",
        "fontSize": "14px",
        "primaryColor": "#EFEFEA",
        "primaryBorderColor": "#0E7C74",
        "primaryTextColor": "#1F2428",
        "lineColor": "#6B7378",
        "clusterBkg": "#F7F7F4",
        "clusterBorder": "#DDDDD6",
        "actorBorder": "#0E7C74",
        "actorBkg": "#EFEFEA",
        "signalColor": "#1F2428",
        "signalTextColor": "#1F2428",
        "noteBkgColor": "#FDF6E3",
        "noteBorderColor": "#C2703D",
    },
}


def render_svg(name: str, source: str, workdir: Path) -> str:
    mmd = workdir / f"{name}.mmd"
    svg = workdir / f"{name}.svg"
    cfg = workdir / "mermaid-config.json"
    mmd.write_text(source)
    cfg.write_text(json.dumps(MERMAID_THEME))
    result = subprocess.run(
        ["npx", "-y", "@mermaid-js/mermaid-cli",
         "-i", str(mmd), "-o", str(svg), "-c", str(cfg), "-b", "transparent"],
        capture_output=True, text=True)
    if result.returncode != 0 or not svg.exists():
        raise SystemExit(f"mermaid-cli failed for '{name}':\n{result.stderr[-2000:]}")
    text = svg.read_text()

    # Namespace every id (+ url(#…) / href="#…" references): seven inline
    # SVGs share one document, and colliding marker ids break arrowheads.
    ids = set(re.findall(r'id="([^"]+)"', text))
    for i in sorted(ids, key=len, reverse=True):
        text = text.replace(f'id="{i}"', f'id="{name}-{i}"')
        text = text.replace(f'url(#{i})', f'url(#{name}-{i})')
        text = text.replace(f'href="#{i}"', f'href="#{name}-{i}"')

    # Drop fixed dimensions from the root; the page CSS scales via viewBox.
    text = re.sub(r'(<svg[^>]*?) width="[^"]*"', r"\1", text, count=1)
    text = re.sub(r'(<svg[^>]*?) height="[^"]*"', r"\1", text, count=1)
    return text


def main() -> int:
    blocks = re.findall(r"```mermaid\n(.*?)```", DIAGRAMS_MD.read_text(), re.S)
    if len(blocks) != len(NAMES):
        raise SystemExit(
            f"DIAGRAMS.md has {len(blocks)} mermaid blocks but NAMES lists "
            f"{len(NAMES)} — update NAMES (and add a tagged <figure> in "
            "overview.html for any new diagram).")

    html = OVERVIEW_HTML.read_text()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for name, block in zip(NAMES, blocks):
            print(f"  rendering {name} …")
            svg_text = render_svg(name, block, workdir)
            # The panel holds exactly one <svg>; match to the </figure>
            # boundary because mermaid SVGs contain inner </div>s.
            pattern = (rf'(<div class="diagram-panel" data-diagram="{name}">)'
                       rf'.*?(</div>\s*</figure>)')
            new_html, n = re.subn(
                pattern, lambda m: m.group(1) + svg_text + m.group(2),
                html, count=1, flags=re.S)
            if n != 1:
                raise SystemExit(
                    f"no <div class=\"diagram-panel\" data-diagram=\"{name}\"> "
                    "panel found in overview.html — add the tagged <figure> first.")
            html = new_html

    OVERVIEW_HTML.write_text(html)
    print(f"OK — {len(NAMES)} diagrams re-rendered into {OVERVIEW_HTML.relative_to(ROOT)}")
    print("Next: re-publish/commit docs/overview.html")
    return 0


if __name__ == "__main__":
    sys.exit(main())
