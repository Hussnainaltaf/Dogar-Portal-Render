"""Server-side inline-style hoister — the engine behind the zero-exception CSP.

WHY THIS MODULE EXISTS
----------------------
Until now the portal's CSP carried exactly one documented exception:
``style-src-attr 'unsafe-inline'``. It existed because the standalone
print/preview documents (letterheads, receipts, the designer's print page)
are built server-side as f-string HTML whose layout is expressed through
per-element ``style="…"`` ATTRIBUTES — many of them computed at request time
(absolute mm coordinates for overlay fields, colour-coded balances, barcode
boxes, base64 background images). CSP has no nonce mechanism for attributes,
so the only way to keep them working was 'unsafe-inline'.

This module removes the need for the exception WITHOUT touching the layout
code. ``hoist_inline_styles()`` post-processes a finished HTML document:

    <div class="doc-bg" style="background-image:url(...);">     -- before

becomes

    <div class="doc-bg" data-csp-h="h0">                        -- after
    ...
    <style nonce="R4nd0m">[data-csp-h="h0"][data-csp-h]{background-image:url(...);}</style>

The declarations end up inside a NONCED <style> element, which the strict
``style-src-elem 'self' 'nonce-…'`` directive explicitly allows. The
``style-src-attr`` directive can then drop to ``'none'`` for these responses.

SECURITY ANALYSIS (read before "improving" this)
------------------------------------------------
* This is NOT a privilege escalation over the old policy. A hoisted rule is
  ``[data-csp-h="hN"][data-csp-h]{ declarations }`` — the *selector* is always
  generated here and only ever targets the element(s) the attribute was found
  on. The attacker-controllable part (the declarations) has exactly the same
  expressive power it had as a style attribute under the old
  ``style-src-attr 'unsafe-inline'``: it can style its own element and nothing
  else. No attacker-chosen selectors, hence no CSS-selector data exfiltration
  — that attack needs an injected <style> ELEMENT, which remains blocked.
* Declarations are sanitised: any declaration containing ``<``, ``{``, ``}``
  or ``@`` is dropped. ``<`` is the load-bearing one — the content model of
  ``<style>`` is raw text that ends at the first ``</style``, and that is true
  even inside a CSS string literal, so a declaration carrying ``<`` could
  otherwise break out of the block. ``{``/``}``/``@`` stop nested rules and
  smuggled ``@import``s.
* The document is parsed with a QUOTE-AWARE scanner, not by regex-splitting on
  ``<script>``/``<style>``. This matters for security, not just tidiness: a
  hostile *attribute value* containing the text ``</style><script>…</script>``
  would fool a regex split into placing a segment boundary in the middle of an
  attribute, so the attribute would never be sanitised and its payload would
  reach the browser verbatim. The scanner determines element boundaries only
  from outside quoted attribute values, so that cannot happen.
* Raw-text/escapable-raw-text element BODIES (``<script>``, ``<style>``,
  ``<textarea>``, ``<title>``) are never rewritten — so a JS template literal
  containing ``style="…"`` survives byte-for-byte.

CASCADE FIDELITY
----------------
An inline style attribute beats every non-important stylesheet rule. To keep
that behaviour the hoisted selector is doubled (``[data-csp-h="hN"]
[data-csp-h]`` → specificity (0,2,0)), which outranks the class/descendant
rules these documents use (verified: none of the document stylesheets use ID
selectors, whose (1,0,0) would win), while staying NON-important — so the
pre-existing ``!important`` print overrides (e.g. ``.doc-bg {
background-size:100% 100% !important }``) keep winning at print time exactly
as they did over the original attribute.

Declaration ORDER is preserved, and each element keeps a single rule, so
within-element precedence (later declaration wins, as in the attribute) is
unchanged. Rules are emitted in first-seen document order.

WHAT THIS DOES NOT COVER
------------------------
Only server-rendered attributes present in the HTML at response time. It does
NOT cover style attributes created in the browser at runtime. In these
documents that distinction is safe: they mutate style through the CSSOM
(``el.style.display``, ``style.setProperty``), which CSP does not police, and
they never call ``setAttribute('style', …)``. Do not tighten
``style-src-attr`` on a response whose scripts do the latter — the app
shell is exactly such a response (``/static/js/safe_dom.js`` sets style
attributes), which is why the tightening is per-response, not global.

The CSSOM exemption is not taken on faith: the designer print page ends up with
a live ``style="--fit-scale: 1;"`` on <body> from
``body.style.setProperty(…)``, and under an enforced ``style-src-attr 'none'``
Chromium reports ZERO violations for it, while the same page's server-rendered
attributes would have been refused. Verified 2026-08-04 with Playwright against
the real responses of all four document routes.

Verified by tests/test_style_hoist.py.
"""
from __future__ import annotations

import html as _html
import re
from typing import Dict, List, Optional, Tuple

__all__ = ["hoist_inline_styles"]

# Elements whose content is raw text (or escapable raw text): their bodies are
# not markup, so a `style="…"` inside them must survive untouched.
_RAW_TEXT_ELEMENTS = frozenset({"script", "style", "textarea", "title"})

_TAG_START_RE = re.compile(r"<(/?)([a-zA-Z][^\s/>]*)")
_ANY_STYLE_ATTR_RE = re.compile(r"style\s*=", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_ATTR_NAME_RE = re.compile(r"[^\s=/>]+")
_ATTR_VALUE_RE = re.compile(
    r"""(\s*=\s*)("([^"]*)"|'([^']*)'|([^\s>]*))""",
)
_BODY_CLOSE_RE = re.compile(r"</body\s*>", re.IGNORECASE)

# Characters that would let a declaration escape the generated <style> element
# or pull in external context. A declaration carrying any of these is dropped
# wholesale — legitimate layout CSS in this codebase never needs them.
_FORBIDDEN_IN_DECL = ("<", "{", "}", "@")


def _mark_hoisted() -> None:
    """Tell the CSP middleware this response needs no style-src-attr.

    Imported lazily and guarded: the hoister is a pure function that unit tests
    call with no request context, and must never fail because of the signal.
    """
    try:
        from app.core.csp_nonce import mark_styles_hoisted
        mark_styles_hoisted()
    except Exception:  # pragma: no cover - defensive
        pass


def _split_declarations(raw: str) -> List[str]:
    """Split a ``prop:value;…`` string on top-level ``;`` only.

    Quote- and paren-aware, because the document backgrounds are inlined as
    ``background-image:url('data:image/png;base64,…')`` — a naive
    ``raw.split(";")`` cuts that data URI in half and the letterhead renders
    blank. This is the single most important detail in the module.
    """
    out: List[str] = []
    buf: List[str] = []
    depth = 0
    quote = ""
    for ch in raw:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
            buf.append(ch)
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
            continue
        if ch == ";" and depth == 0:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        out.append("".join(buf))
    return out


def _sanitize_declarations(raw: str) -> str:
    """Return only the safe declarations of a ``prop:value;…`` string."""
    out: List[str] = []
    for decl in _split_declarations(raw):
        decl = decl.strip()
        if not decl:
            continue
        if any(ch in decl for ch in _FORBIDDEN_IN_DECL):
            continue
        # A declaration needs a property/value separator *before* any
        # parenthesised value, else it is not a declaration at all.
        head = decl.split("(", 1)[0]
        if ":" not in head:
            continue
        out.append(decl)
    return ";".join(out) + (";" if out else "")


def _find_tag_end(doc: str, start: int) -> int:
    """Index just past the ``>`` closing the tag that begins at ``start``.

    Skips over quoted attribute values, so a ``>`` inside ``title=">"`` does
    not end the tag. Returns ``len(doc)`` for an unterminated tag.
    """
    i = start + 1
    n = len(doc)
    quote = ""
    while i < n:
        ch = doc[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in ('"', "'"):
            quote = ch
        elif ch == ">":
            return i + 1
        i += 1
    return n


def _rewrite_start_tag(tag: str, add_rule) -> str:
    """Replace every ``style`` attribute in one start tag with its hook.

    ``add_rule(decls) -> key|None`` registers the declarations and returns the
    hoist key, or ``None`` when nothing safe/usable survived sanitisation (in
    which case the attribute is dropped).

    Attributes are walked one at a time rather than regex-substituted so that
    a ``style=`` appearing *inside another attribute's value* (e.g.
    ``title="style='x'"``) is left alone.
    """
    m = _TAG_START_RE.match(tag)
    if not m:
        return tag
    pos = m.end()
    end = len(tag)
    out: List[str] = [tag[:pos]]
    while pos < end:
        ws = _WS_RE.match(tag, pos)
        if ws:
            out.append(ws.group(0))
            pos = ws.end()
            continue
        ch = tag[pos]
        if ch in (">", "/"):
            out.append(tag[pos:])
            return "".join(out)
        am = _ATTR_NAME_RE.match(tag, pos)
        if not am:
            out.append(ch)
            pos += 1
            continue
        name = am.group(0)
        pos = am.end()
        vm = _ATTR_VALUE_RE.match(tag, pos)
        value: Optional[str] = None
        verbatim = ""
        if vm:
            verbatim = vm.group(0)
            value = (
                vm.group(3) if vm.group(3) is not None
                else vm.group(4) if vm.group(4) is not None
                else vm.group(5)
            )
            pos = vm.end()
        if name.lower() == "style":
            key = add_rule(value or "")
            if key:
                out.append(f'data-csp-h="{key}"')
            # else: attribute dropped entirely
        else:
            out.append(name + verbatim)
    return "".join(out)


def hoist_inline_styles(html_doc: str, nonce: str = "") -> str:
    """Rewrite every ``style="…"`` attribute into a rule in a nonced <style>.

    ``nonce`` — the per-request CSP nonce. If empty, it is auto-fetched from
    the request ContextVar (same convention as the letterhead/receipt
    renderers); outside a request the block is emitted without a nonce, which
    only happens where no CSP header is applied either (unit tests).

    Documents with no style attributes are returned unchanged (no empty
    <style> block is appended), so this is safe to call unconditionally.

    Also signals ``mark_styles_hoisted()`` so CspNonceMiddleware can drop
    ``style-src-attr`` to ``'none'`` for this response.
    """
    _mark_hoisted()

    if not _ANY_STYLE_ATTR_RE.search(html_doc):
        return html_doc

    if not nonce:
        try:
            from app.core.csp_nonce import get_current_csp_nonce
            nonce = get_current_csp_nonce()
        except Exception:  # pragma: no cover - defensive
            nonce = ""

    # decl-string -> hoist key (dedupes identical attribute values)
    seen: Dict[str, str] = {}
    rules: List[Tuple[str, str]] = []

    def add_rule(raw_value: str) -> Optional[str]:
        decls = _sanitize_declarations(_html.unescape(raw_value))
        if not decls:
            return None
        key = seen.get(decls)
        if key is None:
            key = f"h{len(rules)}"
            seen[decls] = key
            rules.append((key, decls))
        return key

    out: List[str] = []
    i = 0
    n = len(html_doc)
    while i < n:
        lt = html_doc.find("<", i)
        if lt < 0:
            out.append(html_doc[i:])
            break
        out.append(html_doc[i:lt])

        # Comments and bogus/declaration markup: copy through untouched.
        if html_doc.startswith("<!--", lt):
            stop = html_doc.find("-->", lt + 4)
            stop = n if stop < 0 else stop + 3
            out.append(html_doc[lt:stop])
            i = stop
            continue

        m = _TAG_START_RE.match(html_doc, lt)
        if not m:
            # A literal "<" in text, "<!DOCTYPE", "<![CDATA[" etc.
            if html_doc.startswith("<!", lt) or html_doc.startswith("<?", lt):
                stop = html_doc.find(">", lt)
                stop = n if stop < 0 else stop + 1
                out.append(html_doc[lt:stop])
                i = stop
            else:
                out.append("<")
                i = lt + 1
            continue

        tag_end = _find_tag_end(html_doc, lt)
        tag = html_doc[lt:tag_end]
        is_close = m.group(1) == "/"
        name = m.group(2).lower()

        if not is_close and name in _RAW_TEXT_ELEMENTS:
            # Emit the start tag (rewriting any style attribute ON the tag
            # itself is still correct) and then the body verbatim.
            out.append(_rewrite_start_tag(tag, add_rule))
            close_re = re.compile(rf"</{re.escape(name)}\s*>", re.IGNORECASE)
            cm = close_re.search(html_doc, tag_end)
            if cm:
                out.append(html_doc[tag_end:cm.end()])
                i = cm.end()
            else:
                out.append(html_doc[tag_end:])
                i = n
            continue

        if is_close:
            out.append(tag)
        else:
            out.append(_rewrite_start_tag(tag, add_rule))
        i = tag_end

    doc = "".join(out)

    if not rules:
        return doc

    n_attr = f' nonce="{nonce}"' if nonce else ""
    css = "".join(
        f'[data-csp-h="{key}"][data-csp-h]{{{decls}}}' for key, decls in rules
    )
    block = f"<style{n_attr}>{css}</style>"

    doc, n_replaced = _BODY_CLOSE_RE.subn(lambda mm: block + mm.group(0), doc, count=1)
    if not n_replaced:
        doc += block
    return doc
