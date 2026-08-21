#!/usr/bin/env python3
"""Mechanically wrap unescaped HTML interpolations in esc(...).

Only handles the cases where the rewrite is provably behaviour-preserving:

  * a bare identifier / property chain            ``c.full_name``
  * such a chain with literal fallbacks           ``c.phone || '—'``
  * an optional chain                             ``e.response?.data?.detail``
  * a parenthesised chain + string method         ``(c.name||'').slice(0,40)``

Everything else (ternaries mixing markup branches, nested template literals,
numeric arithmetic, ``JSON.stringify`` payloads inside attributes) is LEFT ALONE
and must be reviewed by hand — those are exactly the places where a blind
rewrite would either break rendering or give false comfort.

Escaping semantics note
-----------------------
``esc()`` returns '' for null/undefined, so ``esc(a || '—')`` and
``esc(a) || '—'`` differ only when ``a`` is the empty string — in which case
both render the same visible fallback. We wrap the WHOLE expression
(``esc(a || '—')``) to keep the original truthiness chain intact.

Usage:
    python scripts/fix_xss_sinks.py --file app/templates/demands.html
    python scripts/fix_xss_sinks.py --all --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scan_xss_sinks import (  # reuse the audited analyzer  # noqa: E402
    HTML_TAG_RE,
    REPO_ROOT,
    TRUSTED_MARKUP_VARS_BY_FILE,
    is_safe,
    iter_interpolations,
    iter_template_literals,
    normalize,
)

# A property/identifier chain, optionally with ?. and simple [0] indexing.
CHAIN = r"[A-Za-z_$][\w$]*(?:(?:\?\.|\.)[A-Za-z_$][\w$]*|\[\d+\])*"

# Whole expression is a chain, possibly with literal `|| 'x'` / `?? 'x'` tails.
RE_CHAIN_WITH_FALLBACK = re.compile(
    rf"^{CHAIN}(?:\s*(?:\|\||\?\?)\s*(?:'[^'\\]*'|\"[^\"\\]*\"|{CHAIN}))*$"
)

# `(a || '')` style parenthesised chain followed by safe string methods.
RE_PARENS_STRING_METHOD = re.compile(
    rf"^\(\s*{CHAIN}(?:\s*(?:\|\||\?\?)\s*(?:'[^'\\]*'|\"[^\"\\]*\"))?\s*\)"
    r"(?:\.(?:slice|substring|substr|trim|toUpperCase|toLowerCase|replace|split)"
    r"\([^()]*\))*"
    r"(?:\[\d+\])?$"
)

# Reject anything containing these — they need human judgement.
FORBIDDEN = ("`", "JSON.stringify", "=>", "function", "innerHTML")

# A BARE local variable (no dots, no fallback) is never auto-wrapped.
#
# Why: locals in these templates very often hold pre-built TRUSTED MARKUP, e.g.
# login_history.html does
#     const badge = r.status === 'Success' ? '<span …>Success</span>' : '…';
#     …  <td>${badge}</td>
# Wrapping that in esc() would not "fix" anything — it would render the literal
# tags as visible text and break the page. Deciding whether a bare local carries
# markup or data requires reading its assignment, so those are left for manual
# review. Property chains (``r.email``) are different: they read straight off an
# API payload and are always data.
RE_BARE_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")


def is_mechanically_fixable(expr: str) -> bool:
    e = normalize(expr)
    if not e or any(tok in e for tok in FORBIDDEN):
        return False
    if RE_BARE_IDENT.match(e):
        return False
    if "?" in e and ":" in e:  # ternary — review by hand
        # `?.` is fine, a real ternary is not
        stripped = e.replace("?.", "")
        if "?" in stripped and ":" in stripped:
            return False
    if RE_CHAIN_WITH_FALLBACK.match(e):
        return True
    if RE_PARENS_STRING_METHOD.match(e):
        return True
    return False


RE_HANDLER_ATTR = re.compile(r"\bon[a-z]+\s*=\s*(\"|')", re.IGNORECASE)


def _in_handler_attribute(literal: str, offset: int) -> bool:
    """True when ``offset`` inside ``literal`` sits within an on*="…" attribute.

    Scans backwards for the nearest handler-attribute opening quote and checks
    whether that attribute is still open at ``offset``.
    """
    head = literal[:offset]
    last = None
    for m in RE_HANDLER_ATTR.finditer(head):
        last = m
    if last is None:
        return False
    quote = last.group(1)
    # The attribute is still open if its closing quote has not appeared yet.
    return quote not in head[last.end() :]


def fix_file(path: Path, dry_run: bool = False) -> tuple[int, list[str]]:
    rel = path.relative_to(REPO_ROOT).as_posix()
    trusted = frozenset(TRUSTED_MARKUP_VARS_BY_FILE.get(rel, ()))
    source = path.read_text(encoding="utf-8")

    # Collect absolute (start, end) spans of every interpolation needing a wrap.
    edits: list[tuple[int, int, str, str]] = []

    for lit_off, literal in iter_template_literals(source):
        if not HTML_TAG_RE.search(literal):
            continue
        for rel_off, expr in iter_interpolations(literal):
            abs_start = lit_off + rel_off + 2  # skip '${'
            abs_end = abs_start + len(expr)
            if is_safe(expr, trusted):
                continue
            if not is_mechanically_fixable(expr):
                continue
            # Never auto-wrap a value that lands inside an inline event-handler
            # attribute (onclick="f('${x}')"). There the value is inside a JS
            # string, so it needs escJs() — and plain esc() would actively break
            # csp_rebinder.js, whose tokenizer would receive HTML entities
            # instead of the quotes it must parse. Those are fixed by hand.
            if _in_handler_attribute(literal, rel_off):
                continue
            edits.append((abs_start, abs_end, expr, normalize(expr)))

    # Apply right-to-left so earlier offsets stay valid.
    applied: list[str] = []
    out = source
    for start, end, original, norm in sorted(edits, key=lambda t: -t[0]):
        out = out[:start] + f"esc({original})" + out[end:]
        applied.append(norm)

    if not dry_run and applied:
        path.write_text(out, encoding="utf-8")
    return len(applied), applied


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", action="append", default=[], help="repo-relative path")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.all:
        targets = sorted((REPO_ROOT / "app" / "templates").glob("*.html"))
        targets += sorted((REPO_ROOT / "app" / "static" / "js").glob("*.js"))
    else:
        targets = [REPO_ROOT / f for f in args.file]

    grand = 0
    for path in targets:
        if not path.exists():
            print(f"  !! missing {path}")
            continue
        count, applied = fix_file(path, dry_run=args.dry_run)
        grand += count
        if count:
            print(f"{path.relative_to(REPO_ROOT)}: wrapped {count}")
            for a in applied[:200]:
                print(f"    esc({a})")
    print(f"\nTotal wrapped: {grand}{' (dry run)' if args.dry_run else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
