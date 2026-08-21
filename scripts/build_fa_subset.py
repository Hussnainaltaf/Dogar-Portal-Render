#!/usr/bin/env python3
"""Generate a FontAwesome stylesheet containing only the icons this app uses.

WHY
---
`app/static/vendor/css/all.min.css` is the complete FontAwesome 6.4.0 Free
stylesheet: 101,965 bytes, of which 70,799 bytes (69%) are the 1,856 per-icon
`content:` rules for the entire icon set. It is a render-blocking stylesheet in
BOTH shells (the SPA's index.html and the Jinja base.html), so every user pays
for it on every route, and the style engine matches ~1,900 extra selectors on
every recalculation.

The portal uses on the order of 150 icons. This script emits a stylesheet that
keeps FontAwesome's machinery verbatim (font-family bindings, @font-face,
sizing/rotation/animation/stack/list utilities, keyframes, custom properties)
and drops only the `content:` rules for icons that appear nowhere in the source.

SAFETY
------
Dropping an icon rule silently renders a blank box, so the used-set is gathered
from every place a class name can be written:

  * app/frontend/src/**       .tsx .ts   (React SPA)
  * app/templates/**          .html      (legacy Jinja UI)
  * app/static/js/**          .js        (legacy inline/bundled scripts)
  * app/static/css/app.css               (bespoke component CSS)
  * app/**                    .py        (any server-rendered nav/menu config)

Icon names that are *composed at runtime* cannot be found by scanning, so they
are listed explicitly in DYNAMIC below. tests/test_fa_subset.py fails if a
composed name appears in the source without being declared there, and fails if
any scanned token is missing from the generated file — so this cannot silently
rot.

The full stylesheet is deliberately left on disk. Rolling back is a one-line
change to the two <link> tags.

USAGE
    python scripts/build_fa_subset.py            # write + report
    python scripts/build_fa_subset.py --check    # verify the committed file
                                                 # matches what we'd generate
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / "app" / "static" / "vendor" / "css" / "all.min.css"
OUT = ROOT / "app" / "static" / "vendor" / "css" / "fa-subset.min.css"

# Icon names assembled at runtime, which a static scan cannot see.
# Each entry must cite where it comes from so it can be re-verified.
DYNAMIC: dict[str, str] = {
    # app/templates/portal_admin.html: class="fa-solid fa-${t.status==='active'?'pause':'play'}"
    "fa-pause": "portal_admin.html suspend/reactivate toggle",
    "fa-play": "portal_admin.html suspend/reactivate toggle",
    # designer/PalettePanel.tsx and designer/ContentTab.tsx:
    #   `fa-solid fa-chevron-${open ? 'up' : 'down'}`
    "fa-chevron-up": "designer PalettePanel / ContentTab disclosure caret",
    "fa-chevron-down": "designer PalettePanel / ContentTab disclosure caret",
    # designer/PropertiesPanel.tsx: `fa-solid fa-align-${a}` where the map is
    #   (['left', 'center', 'right', 'justify'] as const)
    "fa-align-left": "designer PropertiesPanel text-alignment group",
    "fa-align-center": "designer PropertiesPanel text-alignment group",
    "fa-align-right": "designer PropertiesPanel text-alignment group",
    "fa-align-justify": "designer PropertiesPanel text-alignment group",
}

SCAN: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("app/frontend/src", (".tsx", ".ts")),
    ("app/templates", (".html",)),
    ("app/static/js", (".js",)),
    ("app/static/css", (".css",)),
    ("app", (".py",)),
)

TOKEN = re.compile(r"\bfa-[a-z0-9]+(?:-[a-z0-9]+)*\b")
SKIP_DIRS = {"node_modules", ".git", "venv", "dist", "spa", "__pycache__"}


def collect_used() -> set[str]:
    """Every `fa-*` token written anywhere in the application source."""
    used: set[str] = set(DYNAMIC)
    for rel, exts in SCAN:
        base = ROOT / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in exts:
                continue
            if SKIP_DIRS & set(path.relative_to(ROOT).parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            used.update(TOKEN.findall(text))
    return used


def split_rules(css: str) -> list[str]:
    """Split a stylesheet into top-level rules, honouring nested at-rules."""
    rules: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(css):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                rules.append(css[start : i + 1])
                start = i + 1
    tail = css[start:].strip()
    if tail:
        rules.append(tail)
    return rules


ICON_SELECTOR = re.compile(r"^\.(fa-[a-z0-9-]+):{1,2}before$")


def build(css: str, used: set[str]) -> tuple[str, dict[str, int]]:
    kept: list[str] = []
    stats = {"rules": 0, "icon_rules": 0, "icon_rules_kept": 0, "selectors_dropped": 0}

    for rule in split_rules(css):
        stats["rules"] += 1
        head, _, _ = rule.partition("{")
        selectors = [s.strip() for s in head.strip().split(",") if s.strip()]
        matches = [ICON_SELECTOR.match(s) for s in selectors]

        # Only prune rules whose selectors are *all* per-icon ::before hooks.
        # Anything else (base classes, utilities, @font-face, keyframes, media)
        # is machinery and is copied verbatim.
        if not selectors or not all(matches):
            kept.append(rule)
            continue

        stats["icon_rules"] += 1
        wanted = [
            # strict=True: the lists are built from the same selector split, so a
            # length mismatch would be a logic error, not an input condition.
            sel
            for sel, m in zip(selectors, matches, strict=True)
            if m and m.group(1) in used
        ]
        if not wanted:
            continue
        stats["icon_rules_kept"] += 1
        stats["selectors_dropped"] += len(selectors) - len(wanted)
        if len(wanted) == len(selectors):
            kept.append(rule)
        else:
            body = rule[rule.index("{") :]
            kept.append(",".join(wanted) + body)

    header = (
        "/*! FontAwesome Free 6.4.0 by @fontawesome - https://fontawesome.com\n"
        " * License - https://fontawesome.com/license/free\n"
        " * Copyright 2023 Fonticons, Inc.\n"
        " *\n"
        " * SUBSET - generated by scripts/build_fa_subset.py. Do not edit by hand.\n"
        " * Contains only the icons referenced by this application; the complete\n"
        " * stylesheet remains at all.min.css. Regenerate after adding an icon:\n"
        " *     python scripts/build_fa_subset.py\n"
        " */\n"
    )
    return header + "".join(kept), stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify, do not write")
    args = ap.parse_args()

    css = FULL.read_text(encoding="utf-8")
    used = collect_used()
    out, stats = build(css, used)

    if args.check:
        if not OUT.is_file():
            print(f"MISSING: {OUT}", file=sys.stderr)
            return 1
        if OUT.read_text(encoding="utf-8") != out:
            print(
                f"STALE: {OUT.name} differs from a fresh generation; "
                "run python scripts/build_fa_subset.py",
                file=sys.stderr,
            )
            return 1
        print(f"OK  {OUT.name} is up to date ({len(out)} bytes)")
        return 0

    OUT.write_text(out, encoding="utf-8")
    pct = 100 * (1 - len(out) / len(css))
    print(f"tokens found in source : {len(used)}")
    print(f"top-level rules        : {stats['rules']}")
    print(
        f"icon rules             : {stats['icon_rules']} "
        f"-> kept {stats['icon_rules_kept']} "
        f"(dropped {stats['icon_rules'] - stats['icon_rules_kept']}, "
        f"{stats['selectors_dropped']} alias selectors pruned)"
    )
    print(f"{FULL.name:<24}: {len(css):>7} bytes")
    print(f"{OUT.name:<24}: {len(out):>7} bytes  ({pct:.1f}% smaller)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
