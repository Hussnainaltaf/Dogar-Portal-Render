#!/usr/bin/env python3
"""Static scanner for unescaped HTML-interpolation sinks in Jinja templates.

Why this exists
---------------
The portal's legacy pages build UI fragments as JS template literals and assign
them to ``innerHTML``. Any API / OCR / imported value interpolated into such a
fragment without escaping is a stored-XSS vector: it executes in the browser of
whoever later views the record.

This scanner enumerates every ``${...}`` interpolation that lands inside a
markup-bearing template literal (or a string concatenation assigned to an HTML
sink) and classifies it as SAFE or UNSAFE. It is intentionally conservative:
it prefers false positives over silently missing a sink.

Usage
-----
    python scripts/scan_xss_sinks.py                # report findings
    python scripts/scan_xss_sinks.py --json         # machine-readable
    python scripts/scan_xss_sinks.py --check        # exit 1 if UNSAFE found

``--check`` is what CI / the regression test uses as an XSS ratchet.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "app" / "templates"
STATIC_JS_DIR = REPO_ROOT / "app" / "static" / "js"
# The live frontend is the React SPA. The legacy Jinja tree (TEMPLATE_DIR /
# STATIC_JS_DIR) was removed in the Jinja2 eradication pass; scanning only
# those two directories therefore silently degraded this ratchet to "0 files
# scanned = 0 findings = PASS". The SPA source is now scanned as well so the
# ratchet keeps guarding the code that actually ships.
SPA_SRC_DIR = REPO_ROOT / "app" / "frontend" / "src"

# React's escape hatch. Any `__html:` payload is an innerHTML sink exactly like
# `el.innerHTML = …`, so it is classified with the same SAFE_WRAPPERS rules.
_REACT_HTML_SINK = re.compile(r"__html\s*:\s*(?P<expr>[^}]+)")

# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------

# An interpolation is SAFE when the whole expression is produced by one of these
# escaping / non-string-returning constructs.
SAFE_WRAPPERS = (
    "esc(",
    "_esc(",
    "escAttr(",
    "escUrl(",
    "escJs(",
    "escapeHtml(",
    "escapeHTML(",
    "escapeAttr(",
    "escapeHtmlAttr(",
    "encodeURIComponent(",
    "encodeURI(",
    "window.esc(",
)

# Helper functions in the codebase that provably return trusted, hardcoded
# markup or numbers only (no caller-controlled string passthrough).
SAFE_HELPERS = (
    # Chatbot.tsx: escapes & < > FIRST (so no caller text can open a tag), then
    # applies a fixed **bold** / `code` / newline markdown subset over the
    # already-inert string. Verified at its definition — the only tags it can
    # emit are the hardcoded <strong>/<code>/<br> it adds itself.
    "fmtText(",
    "statusPill(",
    "statusClass(",
    "pillClass(",
    "badgeClass(",
    "fmtNum(",
    "fmtMoney(",
    "fmtDate(",
    # app.js: dayjs(...).format(...) — emits only digits/dashes/colons, or '—'
    "fmtDateTime(",
    # app.js: builds the node with textContent + a [^a-z0-9]-stripped class name,
    # then returns .outerHTML, so the status text is serialised safely.
    "statusBadge(",
    "statusBadgeNode(",
    # client_detail.html: returns one of five hardcoded Tailwind class strings,
    # selected by lowercased === comparisons (default is hardcoded too).
    "candStatusPill(",
    # candidates.html: returns a <span> pill whose class comes from the hardcoded
    # STAGES table and whose label is esc()'d internally (verified at its def).
    "fmtStatus(",
    "money(",
    "num(",
    "Number(",
    "parseInt(",
    "parseFloat(",
    "String(Number(",
)

# Per-line, reviewed waiver. Put this in a trailing comment on the offending
# line ONLY where handling raw markup is the whole point of the code (i.e. the
# sanitizer boundary in safe_dom.js). Grep for it to audit every waiver at once.
SUPPRESS_PRAGMA = "xss-sink-ok"

# Expressions that are structurally non-injectable: pure numeric identifiers,
# array lengths, index counters, boolean ternaries over hardcoded markup.
SAFE_EXPR_PATTERNS = (
    # Numeric-only literals / arithmetic
    re.compile(r"^[\d\s+\-*/().,]+$"),
    # *_ID / *_id / .id / .length / index vars — these are ints from the ORM
    re.compile(r"^[\w.$\[\]]*\b(?:id|ID|Id|length|count|idx|index|i|n|_i)\s*$"),
    re.compile(r"^[\w.$]+\.(?:id|length|size|count)$"),
    # .toLocaleString()/.toFixed() numeric formatting chains. The leading
    # receiver group matters: `re.match` anchors at the start, so without it the
    # pattern only matched an expression that *begins* with ".to" (i.e. never).
    re.compile(r"^[\w.$\[\]]+\.to(?:Locale|Fixed|Precision)\w*\([^)]*\)\s*$"),
    # Loop counters with arithmetic
    re.compile(r"^[\w.$]+\s*[+\-*/]\s*\d+$"),
    # Boolean coercion — !!x can only ever serialise as "true" or "false".
    re.compile(r"^!!\s*[\w.$\[\]?]+$"),
    # Date.now() / new Date().getTime() — integer millisecond clock values.
    re.compile(r"^Date\.now\(\)$"),
    re.compile(r"^new Date\([^)]*\)\.getTime\(\)$"),
)

# Identifiers holding pre-built trusted markup or computed numbers (assigned
# from hardcoded strings / arithmetic earlier in the same function).
#
# ⚠️  Every entry here was read and verified BY HAND during the 2026-07-26 pass.
# Adding a name SUPPRESSES a finding — only do so after confirming the variable
# cannot carry caller/API/OCR-controlled text. When in doubt, escape at the
# interpolation site instead of allow-listing here.
#
# Scoped PER FILE on purpose: a generic name like `label` or `total` is trusted
# in one template and attacker-influenced in another, so a global allow-list
# would silently mask real findings.
TRUSTED_MARKUP_VARS_BY_FILE: dict[str, set[str]] = {
    "app/templates/roles.html": {
        # hardcoded badge/pill markup; pct/have/total are computed integers
        "sysBadge",
        "wildBadge",
        "previewPerms",
        "extra",
        "pct",
        "have",
        "total",
    },
    "app/templates/login_history.html": {
        # dt: dayjs(...).format('YYYY-MM-DD HH:mm') — digits/dashes/colons only.
        # badge: one of two hardcoded <span> literals chosen by an === test.
        "dt",
        "badge",
    },
    "app/templates/dashboard.html": {
        # badge: Tailwind class string selected out of the hardcoded STATUS_BADGE
        # map (miss falls back to a hardcoded default) — no API text reaches it.
        "badge",
    },
    "app/templates/client_detail.html": {
        # infoRow()/gridRow() escape `value` internally unless isHtml=true; every
        # call site passes a hardcoded string literal as `label` (verified).
        "label",
        "v",
    },
    "app/templates/portal_admin.html": {
        # planClr/statClr are selected out of a hardcoded map of Tailwind class
        # strings (the tenant-controlled value is only the lookup key, and a miss
        # falls back to a hardcoded default), so no tenant text reaches the class
        # attribute. pct is a computed integer percentage.
        "planClr",
        "statClr",
        "pct",
    },
    "app/templates/simple_list.html": {
        # from the page's hardcoded COLUMNS/FIELD_MAP config, not from the API
        "SINGULAR",
        "PLURAL",
        # buildFields(): `const val = esc(row[k])` — escaped at assignment so the
        # same value can be reused in both the <input value> and <textarea> body.
        "val",
    },
    "app/templates/base.html": {
        # global-search result row: each of these is escaped AT ASSIGNMENT (see
        # loadGlobalSearch) so the fallback placeholder markup still renders
        "initial",
        "avatar",
        "statusClasses",
        "statusLabel",
        "profession",
        "locText",
        # hardcoded FontAwesome class from the iconForKind/ACT_ICONS maps
        "ico",
        # chatbot fragments assembled from already-escaped pieces
        "html",
        "labelHtml",
    },
    "app/templates/demand_detail.html": {
        # row()/gridCell() escape `value` internally unless isHtml=true; `label`
        # is always a hardcoded string passed at the call site.
        "label",
        "v",
        # computed integer percentage / trusted pill+badge markup / already-escaped
        # fragments assembled earlier in the same function (verified by reading
        # their assignments — see renderCandidateCard / loadPayments / doAssignSearch)
        "fillPct",
        "initial",
        "photo",
        "arabic",
        "pill",
        "badge",
        "onClick",
        "disabled",
        "DEMAND_ID",
    },
    "app/templates/candidates.html": {
        # class-name strings from the hardcoded STAGES / PROFILE_STAGES tables
        "cls",
        "dotCls",
        "lblCls",
        "connector",
        "color",
        "ico",
        # fragments assembled (and escaped) earlier in the same function
        "subtitle",
        "disabled",
        "photo",
        "l",
        "methodLabel",
        "totalFields",
        # renderStatusGrid(): the ONLY use of `v` in this file (verified by grep)
        # is `(isHtml ? (v || '') : esc(v))` — escaped unless the row is expressly
        # flagged as pre-built trusted markup, matching the row()/infoRow()
        # convention in demand_detail.html and client_detail.html.
        "v",
        # scanPassport()'s setOk(trustedHtml) — the deliberately-named markup
        # parameter of the ONE OCR status setter that accepts HTML. Its sibling
        # setters (setBusy/setErr, which receive OCR/backend text) escape with
        # esc(); setOk has a single caller passing a summary built only from
        # `methodLabel` (three hardcoded literals) plus two array lengths.
        # The name is unique to that arrow function (verified by grep), so this
        # entry cannot silently waive an unrelated sink.
        "trustedHtml",
    },
}

# Names that are structurally trusted in EVERY file: markup constants defined as
# hardcoded string literals in this codebase.
TRUSTED_MARKUP_VARS = {
    "EM_DASH",
    "_EM_DASH",
}

# Markers that a template literal is building HTML (vs. a URL or a message).
HTML_TAG_RE = re.compile(r"<\s*/?\s*[a-zA-Z][\w-]*")

# String-concatenation assignments into an HTML sink, e.g.
#   msg.innerHTML = '<i class="fa-solid"></i> ' + err.response.data.detail;
# The markup-bearing opening literal is required so plain-text concatenations
# (e.g. `x.textContent = 'a' + b`) are not reported. Everything up to the end of
# the statement is captured as `rhs` and each `+` operand is judged separately.
RE_CONCAT_SINK = re.compile(
    r"""\.(?:innerHTML|outerHTML)\s*\+?=\s*
        (?P<rhs>
            (?:'[^'\n]*<[^'\n]*'|"[^"\n]*<[^"\n]*")   # opening literal w/ a tag
            \s*\+[^;\n]+                              # ...plus at least one more
        )""",
    re.X,
)

# Sinks that interpret their string argument as HTML.
HTML_SINK_RE = re.compile(
    r"""(?:
          \.innerHTML\s*=
        | \.outerHTML\s*=
        | \.insertAdjacentHTML\s*\(
        | \.safeHTML\s*\(
        | document\.write(?:ln)?\s*\(
        | \$\([^)]*\)\.(?:html|append|prepend|before|after|replaceWith)\s*\(
    )""",
    re.VERBOSE,
)


def iter_template_literals(source: str):
    """Yield (start_offset, raw_literal_text) for every backtick template literal.

    Handles nested ``${ ... `inner` ... }`` by tracking brace depth, and skips
    escaped backticks. Good enough for the hand-written JS in these templates.
    """
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        # Skip line comments
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            j = source.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        # Skip block comments
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            j = source.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        # Skip plain quoted strings
        if ch in "\"'":
            quote, j = ch, i + 1
            while j < n:
                if source[j] == "\\":
                    j += 2
                    continue
                if source[j] == quote or source[j] == "\n":
                    break
                j += 1
            i = j + 1
            continue
        if ch == "`":
            start = i
            j = i + 1
            depth = 0
            while j < n:
                c = source[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "$" and j + 1 < n and source[j + 1] == "{":
                    depth += 1
                    j += 2
                    continue
                if c == "}" and depth > 0:
                    depth -= 1
                    j += 1
                    continue
                if c == "`" and depth == 0:
                    break
                if c == "`" and depth > 0:
                    # nested template literal inside an interpolation
                    k, d2 = j + 1, 0
                    while k < n:
                        if source[k] == "\\":
                            k += 2
                            continue
                        if source[k] == "$" and k + 1 < n and source[k + 1] == "{":
                            d2 += 1
                            k += 2
                            continue
                        if source[k] == "}" and d2 > 0:
                            d2 -= 1
                            k += 1
                            continue
                        if source[k] == "`" and d2 == 0:
                            break
                        k += 1
                    j = k + 1
                    continue
                j += 1
            yield start, source[start : j + 1]
            i = j + 1
            continue
        i += 1


def iter_interpolations(literal: str):
    """Yield the inner expression text of each top-level ``${...}``."""
    i, n = 0, len(literal)
    while i < n - 1:
        if literal[i] == "$" and literal[i + 1] == "{":
            depth, j = 1, i + 2
            start = j
            while j < n and depth:
                c = literal[j]
                if c == "\\":
                    j += 2
                    continue
                if c in "\"'":
                    quote, j = c, j + 1
                    while j < n and literal[j] != quote:
                        j = j + 2 if literal[j] == "\\" else j + 1
                    j += 1
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            yield i, literal[start:j]
            i = j + 1
            continue
        i += 1


def normalize(expr: str) -> str:
    return " ".join(expr.split())


def _is_literal(expr: str) -> bool:
    """True for a quoted string / numeric literal (hardcoded, so trusted)."""
    e = expr.strip()
    if not e:
        return True
    if (e.startswith("'") and e.endswith("'") and len(e) >= 2) or (
        e.startswith('"') and e.endswith('"') and len(e) >= 2
    ):
        # A single quoted literal, not two concatenated ones.
        return e.count(e[0]) == 2
    return bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", e))


def _split_top_level(expr: str, operator: str) -> list[str]:
    """Split on ``operator`` only at paren/bracket/brace depth 0, ignoring strings.

    Needed so `esc(a || b) || '—'` splits into ["esc(a || b)", "'—'"] instead of
    being chopped inside the call, which would misjudge the whole expression.
    """
    parts: list[str] = []
    depth = 0
    i = 0
    start = 0
    n = len(expr)
    oplen = len(operator)
    while i < n:
        c = expr[i]
        if c in "\"'`":
            quote = c
            i += 1
            while i < n:
                if expr[i] == "\\":
                    i += 2
                    continue
                if expr[i] == quote:
                    break
                i += 1
            i += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and expr.startswith(operator, i):
            parts.append(expr[start:i].strip())
            i += oplen
            start = i
            continue
        i += 1
    parts.append(expr[start:].strip())
    return [p for p in parts if p != ""]


def _wraps_whole_expr(expr: str, prefix: str) -> bool:
    """True when ``expr`` is exactly ``prefix(...)`` — the call spans everything.

    Guards against `esc(a) + b`, where the escaper covers only part of the value.
    """
    e = expr.strip()
    if not e.startswith(prefix) or not e.endswith(")"):
        return False
    depth = 0
    i = len(prefix) - 1  # position of the opening paren in the prefix
    n = len(e)
    while i < n:
        c = e[i]
        if c in "\"'`":
            quote = c
            i += 1
            while i < n:
                if e[i] == "\\":
                    i += 2
                    continue
                if e[i] == quote:
                    break
                i += 1
            i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                # The matching close paren must be the last character.
                return i == n - 1
        i += 1
    return False


def is_safe(expr: str, trusted: frozenset[str] = frozenset()) -> bool:
    """Return True when the interpolated expression cannot inject markup.

    ``trusted`` holds the file-scoped names verified as trusted markup/numbers.
    """
    e = normalize(expr)
    if not e:
        return True

    # Nested fragment builder, e.g. `rows.map(r => `<td>${esc(r.x)}</td>`).join('')`.
    # The expression itself emits markup by design; what matters is whether the
    # values interpolated into the NESTED literal are escaped. Recurse into it
    # and judge those, so a correctly-escaped nested builder is not a finding.
    if "`" in e:
        nested_ok = True
        found_nested = False
        for _, nested in iter_template_literals(e):
            found_nested = True
            for _, sub in iter_interpolations(nested):
                if not is_safe(sub, trusted):
                    nested_ok = False
                    break
            if not nested_ok:
                break
        if found_nested and nested_ok:
            return True

    # Fully wrapped in an escaper, e.g. esc(x) / encodeURIComponent(x).
    # `_wraps_whole_expr` confirms the opening paren closes only at the very end,
    # so `esc(a) + b` is NOT mistaken for a fully-escaped expression.
    for wrapper in SAFE_WRAPPERS + SAFE_HELPERS:
        if _wraps_whole_expr(e, wrapper):
            return True

    # Structural safety (ints, lengths, counters)
    for pattern in SAFE_EXPR_PATTERNS:
        if pattern.match(e):
            return True

    # Bare identifier holding pre-built trusted markup
    if e in TRUSTED_MARKUP_VARS or e in trusted:
        return True

    # A redundant outer paren layer must not defeat the checks above, e.g.
    # `(isHtml ? (v || '') : esc(v))`. Unwrap and re-judge the inner expression.
    if e.startswith("(") and _wraps_whole_expr(e, "("):
        if is_safe(e[1:-1], trusted):
            return True

    # Escaper anywhere + no bare concatenated identifier is a heuristic we do
    # NOT accept: composite expressions must be escaped per-operand. But a
    # ternary whose BOTH branches are safe is fine.
    if "?" in e and ":" in e:
        head, _, rest = e.partition("?")
        # Only treat as ternary when '?' is not part of ?. or ??
        if not head.endswith("?") and not rest.startswith(":"):
            # split on the top-level ':'
            depth = 0
            split_at = -1
            for idx, c in enumerate(rest):
                if c in "([{":
                    depth += 1
                elif c in ")]}":
                    depth -= 1
                elif c == ":" and depth == 0:
                    split_at = idx
                    break
            if split_at != -1:
                left = rest[:split_at].strip()
                right = rest[split_at + 1 :].strip()
                # Quoted hardcoded markup counts as trusted
                def branch_ok(b: str) -> bool:
                    if not b:
                        return True
                    if (b.startswith("'") and b.endswith("'")) or (
                        b.startswith('"') and b.endswith('"')
                    ):
                        return True
                    return is_safe(b, trusted)

                if branch_ok(left) and branch_ok(right):
                    return True

    # `a || 'fallback'` / `a ?? b` — safe when EVERY operand is safe. Split at
    # top level only, so `esc(a || b) || '—'` is not chopped inside the call.
    for op in ("||", "??"):
        operands = _split_top_level(e, op)
        if len(operands) > 1:
            if all(_is_literal(o) or is_safe(o, trusted) for o in operands):
                return True

    return False


def scan_file(path: Path):
    rel = path.relative_to(REPO_ROOT).as_posix()
    trusted = frozenset(TRUSTED_MARKUP_VARS_BY_FILE.get(rel, ()))
    source = path.read_text(encoding="utf-8", errors="replace")
    line_starts = [0]
    for idx, c in enumerate(source):
        if c == "\n":
            line_starts.append(idx + 1)

    def line_of(offset: int) -> int:
        lo, hi = 0, len(line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    source_lines = source.splitlines()

    def suppressed(line_no: int) -> bool:
        """True when the finding's line carries an explicit reviewed waiver.

        Used for the handful of places that MUST handle raw markup (the sanitizer
        boundary itself). The pragma is per-line and greppable, so a waiver can
        never silently widen to cover other code the way a name allow-list can.
        """
        if not (1 <= line_no <= len(source_lines)):
            return False
        if SUPPRESS_PRAGMA in source_lines[line_no - 1]:
            return True
        # Also honour the pragma in the contiguous comment block directly above,
        # so a multi-line justification can sit above a long sink line.
        i = line_no - 2
        while i >= 0:
            stripped = source_lines[i].strip()
            if not (stripped.startswith("//") or stripped.startswith("#")):
                break
            if SUPPRESS_PRAGMA in stripped:
                return True
            i -= 1
        return False

    findings = []
    for start, literal in iter_template_literals(source):
        if not HTML_TAG_RE.search(literal):
            continue  # not a markup-bearing literal
        for rel, expr in iter_interpolations(literal):
            if is_safe(expr, trusted):
                continue
            if suppressed(line_of(start + rel)):
                continue
            findings.append(
                {
                    "file": str(path.relative_to(REPO_ROOT)),
                    "line": line_of(start + rel),
                    "expr": normalize(expr)[:160],
                    "kind": "interpolation",
                }
            )

    # ── Concatenation sinks ────────────────────────────────────────────────
    # `el.innerHTML = '<i class="..."></i> ' + someValue` is exactly as
    # dangerous as a template literal but contains no `${...}`, so the pass
    # above cannot see it. This caught the unescaped server `detail` strings on
    # the auth pages (forgot_password / reset_password / contact_support).
    for m in RE_CONCAT_SINK.finditer(source):
        line_no = line_of(m.start())
        if suppressed(line_no):
            continue
        # Judge every top-level `+` operand after the opening markup literal.
        for operand in _split_top_level(normalize(m.group("rhs")), "+"):
            if _is_literal(operand) or is_safe(operand, trusted):
                continue
            findings.append(
                {
                    "file": str(path.relative_to(REPO_ROOT)),
                    "line": line_no,
                    "expr": operand[:160],
                    "kind": "concatenation",
                }
            )
            break  # one finding per sink is enough to fail the ratchet

    # ── React `dangerouslySetInnerHTML` sinks ──────────────────────────────
    # In the SPA this is the ONLY way raw markup reaches the DOM, so it is the
    # direct analogue of `el.innerHTML = …` in the retired Jinja pages. The
    # payload expression is classified with the same SAFE_WRAPPERS rules.
    for m in _REACT_HTML_SINK.finditer(source):
        line_no = line_of(m.start())
        if suppressed(line_no):
            continue
        expr = normalize(m.group("expr")).strip()
        if _is_literal(expr) or is_safe(expr, trusted):
            continue
        findings.append(
            {
                "file": str(path.relative_to(REPO_ROOT)),
                "line": line_no,
                "expr": expr[:160],
                "kind": "react-dangerously-set-inner-html",
            }
        )
    return findings


def _scan_targets() -> list[Path]:
    """Every file the ratchet must inspect.

    Historically this was the Jinja tree only. After the Jinja2 eradication
    pass those directories no longer exist, so the SPA source is scanned too —
    otherwise the ratchet would pass vacuously on an empty file list.
    """
    targets: list[Path] = []
    if TEMPLATE_DIR.is_dir():
        targets += sorted(TEMPLATE_DIR.glob("*.html"))
    if STATIC_JS_DIR.is_dir():
        targets += sorted(STATIC_JS_DIR.glob("*.js"))
    if SPA_SRC_DIR.is_dir():
        for ext in ("*.ts", "*.tsx"):
            targets += sorted(SPA_SRC_DIR.rglob(ext))
    return targets


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--check", action="store_true", help="exit 1 when UNSAFE found")
    args = ap.parse_args()

    targets = _scan_targets()
    if not targets:
        print(
            "FAIL: the XSS scanner found NO files to scan. A ratchet that "
            "scans nothing always passes — check the scan roots.",
            file=sys.stderr,
        )
        return 1

    all_findings = []
    for path in targets:
        all_findings.extend(scan_file(path))

    if args.json:
        print(json.dumps(all_findings, indent=2))
    else:
        by_file: dict[str, list] = {}
        for f in all_findings:
            by_file.setdefault(f["file"], []).append(f)
        total = len(all_findings)
        print(f"Unescaped HTML interpolation sinks: {total}")
        for fname in sorted(by_file, key=lambda k: -len(by_file[k])):
            items = by_file[fname]
            print(f"\n  {fname}  ({len(items)})")
            for it in items:
                print(f"    L{it['line']:>6}  {it['expr']}")

    if args.check and all_findings:
        print(
            f"\nFAIL: {len(all_findings)} unescaped interpolation(s) reachable by innerHTML.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
