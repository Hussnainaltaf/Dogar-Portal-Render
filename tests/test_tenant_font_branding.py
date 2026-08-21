"""Per-tenant typeface branding: the contract, actually executed.

WHY THIS MODULE EXISTS (2026-08-19)
===================================
The operator asked for the topbar and document branding to follow the active
office automatically and named three things: "logo/colors/fonts". Logo and
colour worked. The FONT was a complete no-op end to end, and it is worth
recording exactly how that survived a green suite, because every individual
piece looked correct in isolation:

  * ``applyTenantBranding()`` read ``(tenant as any).fontKey`` -- the ``as any``
    cast meant tsc could not object to a property that did not exist;
  * ``fontKey`` was absent from the boot payload in app/main.py, so the value
    read was always ``undefined``;
  * ``resolveFontStack(undefined)`` returns the SYSTEM stack rather than
    throwing, so every office silently rendered in the default font;
  * ``--brand-font`` was written to ``<html>`` and NO CSS rule anywhere read it,
    so even a correct value would have changed nothing; and
  * there was no column to store a font in at all.

Five separate links, each individually defensible, adding up to a feature that
did nothing. No test could fail, because no test looked at the chain.

WHAT THESE TESTS PIN
--------------------
1. The allow-list exists in BOTH languages and the two sets are IDENTICAL. This
   is the same class of defect as the coordinate engine's duplicated ASCENT: one
   constant, authored twice, in two languages, with drift invisible until a
   document prints in the wrong typeface.
2. The value that leaves the API is a KEY, never a resolved ``font-family``
   declaration -- because it is interpolated into live CSS.
3. The API REFUSES an unknown key instead of storing it. A stored-but-unusable
   value is a setting that lies about itself.
4. ``--brand-font`` is actually consumed by a rule, and consumed WITH A FALLBACK,
   which is what keeps the Dogar root portal's own typography untouched.
5. The boot payload carries the key, so the frontend has something to resolve.
6. The Dogar/no-tenant path applies no font at all.

Each test asserts on the mechanism, not on a call site, so a refactor cannot
turn it into a silent no-op.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from app.core.branding_contract import (
    ALLOWED_FONT_KEYS,
    DEFAULT_FONT_KEY,
    FONT_STACKS_PRINT,
    normalise_font_key,
    print_font_stack,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BRANDING_TS = REPO_ROOT / "app" / "frontend" / "src" / "lib" / "branding.ts"
API_TS = REPO_ROOT / "app" / "frontend" / "src" / "lib" / "api.ts"
INDEX_CSS = REPO_ROOT / "app" / "frontend" / "src" / "index.css"
MAIN_PY = REPO_ROOT / "app" / "main.py"


def _ts_font_keys() -> set[str]:
    """The keys of FONT_STACKS in branding.ts, read from the actual source.

    Parsed rather than hardcoded here: a copy of the list in this test file
    would just be a THIRD place to drift.
    """
    src = BRANDING_TS.read_text(encoding="utf-8")
    m = re.search(
        r"export const FONT_STACKS:\s*Record<string,\s*string>\s*=\s*\{(.*?)\n\}",
        src,
        re.S,
    )
    assert m, "FONT_STACKS is no longer declared in branding.ts in a parseable form"
    body = m.group(1)
    # Keys are bare identifiers at the start of a line: `inter: '...'`
    return set(re.findall(r"^\s*([A-Za-z_][\w-]*)\s*:", body, re.M))


# ---------------------------------------------------------------------------
# 1. The allow-list cannot drift between the two languages
# ---------------------------------------------------------------------------
def test_the_font_allow_list_is_identical_in_python_and_typescript():
    """The coordinate engine's exact failure mode, pre-empted.

    A font present on one side only means: the API accepts a key the browser
    cannot resolve (office sees the default and thinks the save failed), or the
    browser offers a key the API rejects with a 422 on save.
    """
    ts_keys = _ts_font_keys()
    assert ts_keys, "parsed no font keys out of branding.ts"
    assert ts_keys == set(ALLOWED_FONT_KEYS), (
        "the font allow-lists have drifted apart.\n"
        f"  TypeScript (branding.ts): {sorted(ts_keys)}\n"
        f"  Python (branding_contract): {sorted(ALLOWED_FONT_KEYS)}\n"
        f"  only in TS: {sorted(ts_keys - set(ALLOWED_FONT_KEYS))}\n"
        f"  only in PY: {sorted(set(ALLOWED_FONT_KEYS) - ts_keys)}"
    )


def test_the_print_renderer_can_resolve_every_allowed_key():
    """No key may be accepted by the API that the document path cannot draw.

    Otherwise a tenant configures a font, the screen honours it, and the printed
    document raises a KeyError -- turning a cosmetic setting into a 500 on the
    one workflow that matters most.
    """
    missing = sorted(set(ALLOWED_FONT_KEYS) - set(FONT_STACKS_PRINT))
    assert not missing, (
        f"{missing} are accepted by the API but have no print stack, so a "
        "document rendered for such a tenant would fail"
    )


def test_the_default_key_is_itself_allowed():
    """A default outside the allow-list would make normalisation loop-holed."""
    assert DEFAULT_FONT_KEY in ALLOWED_FONT_KEYS


# ---------------------------------------------------------------------------
# 2. Stored values are KEYS, never CSS declarations
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "hostile",
    [
        "}; body { display:none }",
        "Comic Sans MS",
        "inter; color:red",
        "../../etc/passwd",
        "SYSTEM",           # case is folded, not rejected outright
        "",
        None,
        "'",
    ],
)
def test_normalisation_never_returns_anything_outside_the_allow_list(hostile):
    """Rendering must never emit an attacker- or accident-supplied declaration.

    This is the render-time backstop, not the validation gate: by the time a
    value is being drawn, refusing would leave an office with no typography at
    all, which is worse than the default.
    """
    out = normalise_font_key(hostile)
    assert out in ALLOWED_FONT_KEYS, f"{hostile!r} normalised to {out!r}"


def test_no_stack_can_terminate_the_css_rule_it_is_written_into():
    """The stacks themselves are interpolated into live CSS, so they are checked too.

    `--brand-font: <value>;` -- a `}` or `;` inside <value> ends the declaration
    or the rule early and breaks the surrounding stylesheet.
    """
    for key, stack in FONT_STACKS_PRINT.items():
        assert "}" not in stack and ";" not in stack, (
            f"print stack for {key!r} contains a CSS terminator: {stack!r}"
        )
    src = BRANDING_TS.read_text(encoding="utf-8")
    m = re.search(
        r"export const FONT_STACKS:\s*Record<string,\s*string>\s*=\s*\{(.*?)\n\}",
        src,
        re.S,
    )
    assert m
    assert "}" not in m.group(1).replace("\\}", ""), (
        "a TypeScript font stack contains a brace, which would terminate the "
        "CSS rule it is written into"
    )


def test_the_print_resolver_returns_a_declaration_not_a_key():
    """The two are different values and must not be confused.

    ``print_font_stack`` is the ONLY place a key becomes a declaration, so if it
    ever returned the key itself the document would emit
    `font-family: serif-key` and fall back to the browser default with no error.
    """
    stack = print_font_stack("serif")
    assert "," in stack, f"{stack!r} does not look like a font stack"
    assert stack != "serif"
    assert print_font_stack("nonsense-key") == FONT_STACKS_PRINT[DEFAULT_FONT_KEY]


# ---------------------------------------------------------------------------
# 3. The API refuses unknown keys rather than storing them
# ---------------------------------------------------------------------------
def test_the_branding_payload_rejects_an_unknown_font_key():
    """A validation error, not a silently-stored dud.

    ``update_tenant_branding`` applies the payload with a blind
    ``setattr(t, k, v)`` loop, so anything this model accepts lands in the
    column verbatim.
    """
    from pydantic import ValidationError

    from app.api.endpoints.tenants import BrandingUpdate

    with pytest.raises(ValidationError):
        BrandingUpdate(font_key="Comic Sans MS")
    with pytest.raises(ValidationError):
        BrandingUpdate(font_key="}; body{display:none}")


def test_the_branding_payload_accepts_every_allowed_key():
    """The mirror image: a legitimate choice must not 422."""
    from app.api.endpoints.tenants import BrandingUpdate

    for key in sorted(ALLOWED_FONT_KEYS):
        assert BrandingUpdate(font_key=key).font_key == key


def test_the_branding_payload_folds_case_and_blanks():
    """An empty select means "use the default", not "store a blank"."""
    from app.api.endpoints.tenants import BrandingUpdate

    assert BrandingUpdate(font_key="INTER").font_key == "inter"
    assert BrandingUpdate(font_key="  Serif ").font_key == "serif"
    assert BrandingUpdate(font_key="").font_key == DEFAULT_FONT_KEY


def test_omitting_the_font_key_leaves_it_unset_not_defaulted():
    """`exclude_unset=True` in the endpoint depends on this.

    If the model materialised a default for an absent field, saving the office
    ADDRESS would also silently overwrite the office's chosen font.
    """
    from app.api.endpoints.tenants import BrandingUpdate

    payload = BrandingUpdate(office_name="Lahore Office")
    assert "font_key" not in payload.model_dump(exclude_unset=True)


# ---------------------------------------------------------------------------
# 4. The chain that was broken: column -> payload -> type -> CSS
# ---------------------------------------------------------------------------
def test_the_tenant_model_has_somewhere_to_store_the_font():
    from app.models import Tenant

    assert hasattr(Tenant, "font_key"), (
        "Tenant has no font_key column, so the branding form has nowhere to "
        "save the office's typeface"
    )


def test_the_boot_payload_carries_the_font_key():
    """Without this the frontend resolves `undefined` -> the default, forever."""
    src = MAIN_PY.read_text(encoding="utf-8")
    assert '"fontKey"' in src, (
        "app/main.py no longer injects fontKey into window.__TENANT_BOOT__, so "
        "the SPA has no font to apply no matter what the office configured"
    )


def test_the_tenant_info_type_declares_the_font_key():
    """The `as any` cast is what let this field stay missing without a build error."""
    src = API_TS.read_text(encoding="utf-8")
    m = re.search(r"export interface TenantInfo\s*\{(.*?)\n\}", src, re.S)
    assert m, "TenantInfo is no longer declared in a parseable form"
    assert re.search(r"^\s*fontKey\??:", m.group(1), re.M), (
        "TenantInfo does not declare fontKey, so branding.ts has to reach for "
        "it with a cast and tsc cannot check the boot contract"
    )


def test_branding_ts_reads_the_font_key_without_an_any_cast():
    """The cast is the camouflage; its absence is the protection."""
    src = BRANDING_TS.read_text(encoding="utf-8")
    assert "tenant.fontKey" in src, "branding.ts no longer reads tenant.fontKey"
    assert "(tenant as any).fontKey" not in src, (
        "the `as any` cast is back: with it, removing fontKey from the boot "
        "payload or from TenantInfo compiles cleanly and the font silently "
        "stops working again"
    )


def test_some_css_rule_actually_consumes_the_brand_font_token():
    """The write existed for a day with no reader at all.

    Asserted on the STYLESHEET, not on the JS: a token nobody reads is
    indistinguishable from a token nobody writes, and the JS side already had a
    test-shaped comment claiming it worked.
    """
    css = INDEX_CSS.read_text(encoding="utf-8")
    consumers = re.findall(r"font-family:\s*var\(\s*--brand-font", css)
    assert consumers, (
        "no rule in index.css reads --brand-font, so applyTenantBranding() "
        "writes a custom property that changes nothing on screen"
    )


def test_the_brand_font_is_consumed_with_a_fallback_so_dogar_is_unchanged():
    """The hard requirement of the brief, enforced structurally.

    On the root portal ``applyTenantBranding()`` is a deliberate no-op, so
    ``--brand-font`` is never set. A bare ``var(--brand-font)`` would then
    resolve to nothing and Dogar would lose its own typography; a hardcoded
    stack would override it. Only the fallback form leaves Dogar byte-identical.
    """
    css = INDEX_CSS.read_text(encoding="utf-8")
    m = re.search(r"font-family:\s*var\(\s*--brand-font\s*,([^)]*\)?[^;]*);", css)
    assert m, (
        "--brand-font is consumed WITHOUT a fallback. On the Dogar root portal "
        "the property is never set, so this would resolve to nothing and the "
        "root portal would lose the typography it authors itself."
    )
    assert "--font-body" in m.group(1), (
        f"the fallback is {m.group(1)!r}, which is not Dogar's authored body "
        "font token -- the root portal's typography would change"
    )


def test_applying_branding_without_a_tenant_is_a_no_op_in_source():
    """Dogar must never be re-skinned by the tenant path.

    Checked in the source because there is no DOM here; the browser-side
    behaviour is covered by the UI harness.
    """
    src = BRANDING_TS.read_text(encoding="utf-8")
    m = re.search(r"export function applyTenantBranding\((.*?)\n\}", src, re.S)
    assert m, "applyTenantBranding is no longer declared in a parseable form"
    body = m.group(1)
    assert re.search(r"if\s*\(!root\s*\|\|\s*!tenant\)\s*return null", body), (
        "applyTenantBranding no longer bails out when no tenant is resolved, "
        "so the Dogar root portal would be re-skinned by the tenant branding "
        "path -- explicitly forbidden by the brief"
    )
    # The font write must sit AFTER the guard, not before it.
    guard_at = body.index("!tenant")
    font_at = body.index("--brand-font")
    assert font_at > guard_at, (
        "--brand-font is written before the no-tenant guard, so the Dogar "
        "portal would have its typeface overwritten"
    )


# ---------------------------------------------------------------------------
# 5. DOCUMENT branding -- the half the operator named that reached no document
#
# MEASURED GAP 2026-08-19.
#
# `letterhead_renderer._wrap_document()` has THIRTEEN call sites, and exactly
# ONE of them (render_noc_verification) ever received a `tenant=` argument.
# Every other document type -- the main letter, both undertakings, the protector
# packet, the demand letter, the visa and roman undertakings, the permission
# request, the e-barcode -- physically could not know which office it was being
# printed for. The tenants table could hold an office's colour and typeface and
# every one of those letters would still print in Dogar's, with nothing failing
# anywhere and no wrong pixel to notice.
#
# The fix routes branding through `_styles_for()`, the ONE function all of them
# resolve their stylesheet from, and reads the active office from a
# request-scoped ContextVar rather than adding a parameter to thirteen
# signatures (thirteen chances to miss one, each silent).
#
# These tests assert on that choke point and on the invariant that matters --
# Dogar's documents are unchanged -- rather than on any single document type.
# ---------------------------------------------------------------------------
def test_the_document_stylesheet_carries_no_unreplaced_font_token():
    """A leaked `%DOCFONT%` would make `font-family` invalid.

    The document would still render -- in the browser's default font -- which is
    the silent-wrongness failure mode this whole module exists to prevent.
    """
    from app.services.letterhead_renderer import _styles_for

    css = _styles_for()
    assert "%DOCFONT%" not in css, (
        "the document stylesheet still contains the %DOCFONT% placeholder, so "
        "font-family is invalid and every document falls back to the browser "
        "default"
    )


def test_dogar_documents_keep_their_authored_typeface():
    """The hard requirement: the root portal's documents do not change.

    Asserted against the literal authored stack, not against "whatever the
    default resolves to" -- the point is that this specific value survives.
    """
    import re

    from app.core.branding_contract import DOGAR_DOCUMENT_FONT
    from app.services.letterhead_renderer import _styles_for

    css = _styles_for()
    m = re.search(r"html, body \{[^}]*font-family:\s*([^;]+);", css)
    assert m, "the document stylesheet no longer sets font-family on html, body"
    assert m.group(1).strip() == DOGAR_DOCUMENT_FONT, (
        f"Dogar's documents would now render in {m.group(1).strip()!r} instead "
        f"of the authored {DOGAR_DOCUMENT_FONT!r}"
    )


def test_a_tenants_documents_render_in_that_tenants_typeface():
    """The feature itself, at the choke point every document shares."""
    import re

    from app.services.letterhead_renderer import _styles_for

    class _T:
        font_key = "serif"

    css = _styles_for(tenant=_T())
    m = re.search(r"html, body \{[^}]*font-family:\s*([^;]+);", css)
    assert m
    assert "Georgia" in m.group(1), (
        f"a tenant configured for the serif stack rendered {m.group(1)!r} -- the "
        "office's document typeface is being ignored"
    )


def test_two_different_offices_get_two_different_documents():
    """Guards against a resolver that reads the column but returns a constant.

    A single-tenant assertion would pass if every office got the same
    (non-Dogar) font, which is the same bug wearing a different value.
    """
    import re

    from app.services.letterhead_renderer import _styles_for

    def font_of(key):
        class _T:
            font_key = key
        m = re.search(
            r"html, body \{[^}]*font-family:\s*([^;]+);", _styles_for(tenant=_T())
        )
        return m.group(1).strip()

    assert font_of("serif") != font_of("mono"), (
        "two offices with different configured typefaces produced identical "
        "documents"
    )


def test_a_hostile_font_value_cannot_inject_css_into_a_document():
    """The stylesheet is built by string substitution, so this is load-bearing.

    Compared against the UNBRANDED stylesheet with the font declaration masked:
    the two must be otherwise identical, which proves nothing was added, removed
    or reopened -- a stronger statement than "the string I thought of is absent".
    """
    import re

    from app.services.letterhead_renderer import _styles_for

    class _Evil:
        font_key = '"}; body{display:none} .sheet{'

    base = _styles_for()
    hostile = _styles_for(tenant=_Evil())
    mask = lambda s: re.sub(r"font-family:\s*[^;]+;", "FONT;", s, count=1)  # noqa: E731
    assert mask(base) == mask(hostile), (
        "a hostile font_key changed the document stylesheet beyond its "
        "font-family declaration -- CSS injection into the letterhead"
    )
    assert "body{display:none}" not in hostile


def test_the_resolved_tenant_snapshot_carries_the_font_key():
    """The renderer only ever sees this snapshot, never the ORM row.

    `features` had exactly this defect once already: stored on the row, editable
    in admin, and absent from the snapshot -- so nothing in the application
    could read it. A branding value missing here is a value no document can use.
    """
    import dataclasses

    from app.core.tenancy import ResolvedTenant

    names = {f.name for f in dataclasses.fields(ResolvedTenant)}
    assert "font_key" in names, (
        "ResolvedTenant has no font_key field, so the document renderer -- which "
        "receives only this snapshot -- can never learn the office's typeface"
    )


def test_the_middleware_publishes_the_tenant_for_renderers_to_find():
    """Without the publish, the ContextVar is always None and every document
    silently falls back to Dogar branding."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "core" / "tenancy.py").read_text(
        encoding="utf-8"
    )
    assert "current_tenant.set(" in src, (
        "TenantResolverMiddleware no longer publishes the resolved tenant to "
        "the branding ContextVar, so document branding reverts to Dogar for "
        "every office"
    )


def test_the_active_tenant_is_read_when_none_is_passed_explicitly():
    """The ContextVar path is what serves twelve of the thirteen document types.

    Testing only the explicit-argument path would leave the mechanism that
    actually runs in production unverified.
    """
    import re

    from app.core.branding_contract import current_tenant
    from app.services.letterhead_renderer import _styles_for

    class _T:
        font_key = "mono"

    token = current_tenant.set(_T())
    try:
        css = _styles_for()  # no tenant= argument, exactly like the 12 sites
        m = re.search(r"html, body \{[^}]*font-family:\s*([^;]+);", css)
        assert m
        assert "Mono" in m.group(1) or "Courier" in m.group(1), (
            f"the active office was ignored when no tenant was passed "
            f"explicitly: got {m.group(1)!r}"
        )
    finally:
        current_tenant.reset(token)


def test_an_explicit_tenant_outranks_the_active_one():
    """The one existing `tenant=` call site must keep working and stay testable."""
    import re

    from app.core.branding_contract import current_tenant
    from app.services.letterhead_renderer import _styles_for

    class _Ctx:
        font_key = "mono"

    class _Explicit:
        font_key = "serif"

    token = current_tenant.set(_Ctx())
    try:
        m = re.search(
            r"html, body \{[^}]*font-family:\s*([^;]+);",
            _styles_for(tenant=_Explicit()),
        )
        assert m and "Georgia" in m.group(1), (
            "an explicitly-passed tenant was overridden by the ambient one"
        )
    finally:
        current_tenant.reset(token)


def test_no_active_tenant_means_dogar_not_a_tenant_default():
    """`document_font_stack(None)` must return the AUTHORED serif stack.

    Folding to the "system" key would quietly switch Dogar's documents to a
    sans-serif face -- a change to the root portal's identity, which the brief
    forbids.
    """
    from app.core.branding_contract import (
        DOGAR_DOCUMENT_FONT,
        FONT_STACKS_PRINT,
        document_font_stack,
    )

    assert document_font_stack(None) == DOGAR_DOCUMENT_FONT
    assert DOGAR_DOCUMENT_FONT != FONT_STACKS_PRINT["system"], (
        "the Dogar document font is the same value as the tenant default, so "
        "this test could no longer tell the two apart"
    )


# ---------------------------------------------------------------------------
# 6. WHERE the branding is applied -- the defect the browser found and every
#    source-level test above missed
#
# MEASURED DEFECT 2026-08-19, found by a real Chromium probe, not by any of the
# 34 tests that preceded this section.
#
# Every test above verifies that the branding MECHANISM is correct: the column
# exists, the payload carries the key, the cast is gone, a CSS rule reads the
# token with a Dogar-preserving fallback, the API validates, the documents
# resolve. All true. All green. And the tenant portal still rendered in Dogar
# blue with Dogar's typeface, because the single call site was:
#
#     AppLayout.tsx:52   useEffect(() => { applyTenantBranding(TENANT_INFO) }, [])
#
# and AppLayout is mounted INSIDE <RequireAuth>. So:
#
#   * the tenant's LOGIN page -- the first screen their staff see, the one that
#     shows the office name and logo -- was never branded;
#   * neither were /forgot-password, /reset-password, /change-password or
#     /contact-support;
#   * and any 401 unmounts AppLayout, so the office branding vanished at exactly
#     the moment a user was being shown a branding-heavy screen.
#
# A correct mechanism wired into the wrong place is still a broken feature. The
# lesson for this file: "does the code do the right thing" and "does the code
# run where it needs to" are two different questions, and only the second one
# fails silently. These tests ask the second question.
# ---------------------------------------------------------------------------

MAIN_TSX = REPO_ROOT / "app" / "frontend" / "src" / "main.tsx"
APP_LAYOUT_TSX = REPO_ROOT / "app" / "frontend" / "src" / "layout" / "AppLayout.tsx"
APP_TSX = REPO_ROOT / "app" / "frontend" / "src" / "App.tsx"


def _is_comment_line(line: str) -> bool:
    """Whether a source line is prose rather than code.

    Needed because this module's OWN documentation, and the explanatory block
    left in AppLayout, both spell the identifier out. My first version of this
    helper counted branding.ts itself as a second call site purely on the
    strength of its docstring -- a test failing for a reason that has nothing
    to do with the behaviour under test is worse than no test.
    """
    s = line.lstrip()
    return s.startswith("//") or s.startswith("*") or s.startswith("/*")


def _call_sites() -> dict[Path, str]:
    """Every SPA source file that CALLS applyTenantBranding.

    Deliberately distinguishes a call from a mention: the import line, the
    module docstring and the explanatory comments all contain the identifier,
    and counting those would make this test pass -- or fail -- for the wrong
    reason. The `export function` declaration is excluded for the same reason.
    """
    src_dir = REPO_ROOT / "app" / "frontend" / "src"
    hits: dict[Path, str] = {}
    for path in sorted(src_dir.rglob("*.ts")) + sorted(src_dir.rglob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"(?<![\w.`])applyTenantBranding\s*\(", text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            line = text[line_start:text.find("\n", m.start())]
            if _is_comment_line(line):
                continue
            if re.search(r"\bfunction\s+$", text[line_start:m.start()]):
                continue
            hits[path] = text
            break
    return hits


def test_branding_is_applied_outside_the_authentication_gate():
    """The defect itself: branding that only runs for logged-in users.

    Asserted on the module the call lives in, because that is what determines
    whether it runs before or after <RequireAuth>. Applying it in main.tsx runs
    it for every route including the login page; applying it inside AppLayout
    runs it for none of them.
    """
    sites = _call_sites()
    assert sites, (
        "nothing in the SPA calls applyTenantBranding any more, so no tenant "
        "portal is branded at all"
    )
    gated = {p for p in sites if p.name in {"AppLayout.tsx", "Sidebar.tsx", "Topbar.tsx"}}
    assert not gated, (
        "applyTenantBranding is called from "
        f"{sorted(p.name for p in gated)}, which mount INSIDE <RequireAuth>. "
        "The tenant's own login page would then render in Dogar's colours and "
        "typeface, and the branding would disappear again on every 401."
    )
    assert MAIN_TSX in sites, (
        "applyTenantBranding is not called from main.tsx. It must run at boot, "
        "before the router mounts, so that unauthenticated routes (login, "
        "password reset, contact support) carry the office's branding too."
    )


def test_the_branding_call_runs_before_react_renders():
    """Order matters, not just location.

    Inside main.tsx the call could still be placed after `createRoot(...).render`
    -- which would technically be outside the auth gate but would re-introduce a
    first-paint flash of Dogar branding on a partner office's portal.
    """
    # Comments are stripped first. The explanatory block above the call names
    # `createRoot` in prose, and matching that mention made this test fail while
    # the code was in fact correct -- measuring the documentation instead of the
    # program.
    src = "\n".join(
        "" if _is_comment_line(ln) else ln
        for ln in MAIN_TSX.read_text(encoding="utf-8").splitlines()
    )
    call_at = src.index("applyTenantBranding(TENANT_INFO)")
    render_at = src.index("createRoot")
    assert call_at < render_at, (
        "applyTenantBranding runs after createRoot(), so the first painted "
        "frame of a tenant portal uses Dogar's branding before being repainted"
    )

    # STRENGTHENED 2026-08-20, and only because a mutation exposed the gap.
    # Re-anchoring mutation BO onto `queueMicrotask(() => applyTenantBranding(…))`
    # produced code that this test still PASSED: the call is outside the auth
    # gate and textually above createRoot, so both assertions above hold -- and
    # the tokens are nonetheless written after the first frame is composited,
    # which is the exact flash the test claims to prevent. Position was standing
    # in for timing. Now the timing is asserted directly.
    line = next(
        ln for ln in src.splitlines() if "applyTenantBranding(TENANT_INFO)" in ln
    )
    for deferrer in ("queueMicrotask", "setTimeout", "requestAnimationFrame",
                     "requestIdleCallback", "Promise.resolve", ".then("):
        assert deferrer not in line, (
            f"the branding call is deferred via {deferrer!r}: {line.strip()!r}. "
            "It is then applied after the first paint, so a partner office's "
            "portal still flashes Dogar's colours -- the very defect the "
            "boot-time placement exists to remove."
        )


def test_the_login_route_is_not_behind_the_auth_gate():
    """Anchors the premise of the test above.

    If /login were itself inside <RequireAuth> then applying branding at boot
    would be pointless, and this whole section would be reasoning about a route
    that cannot render. Read from App.tsx so a routing refactor is caught here
    rather than by a user.
    """
    src = APP_TSX.read_text(encoding="utf-8")
    login_at = src.index('path="/login"')
    guard_at = src.index("<RequireAuth>")
    assert login_at < guard_at, (
        "the /login route is no longer declared ahead of the <RequireAuth> "
        "block; the unauthenticated branding path this section protects may no "
        "longer exist"
    )


def test_there_is_exactly_one_branding_writer():
    """Two writers of the same tokens is how the next drift starts.

    The old AppLayout effect and a new boot call would both be "correct" in
    isolation while disagreeing about when the tokens are valid, and a later
    edit to one would silently not affect the other.
    """
    sites = _call_sites()
    assert len(sites) == 1, (
        "applyTenantBranding is called from "
        f"{sorted(p.name for p in sites)}. Exactly one module must own the "
        "brand tokens."
    )


def test_app_layout_does_not_import_the_token_writer():
    """AppLayout must not be able to re-acquire the ability to write the tokens.

    NARROWED 2026-08-19. This test first forbade AppLayout from importing
    @/lib/branding at all, which was wrong: the module also exports pure NAME
    helpers, and AppLayout legitimately needs `brandFullName` for the footer's
    copyright holder. Banning the whole module would have pushed the next
    author into hardcoding "Dogar Trading Corporation" again -- forbidding the
    correct fix in the name of preventing the wrong one.

    What actually matters is the specific import that lets this component write
    the brand tokens, so that is what is checked. `test_there_is_exactly_one_
    branding_writer` covers the call itself; this covers the capability.
    """
    src = APP_LAYOUT_TSX.read_text(encoding="utf-8")
    imports = re.findall(r"import\s*\{([^}]*)\}\s*from\s*'@/lib/branding'", src)
    named = {n.strip() for group in imports for n in group.split(",") if n.strip()}
    assert "applyTenantBranding" not in named, (
        "AppLayout imports applyTenantBranding again. It mounts inside "
        "<RequireAuth>, so branding applied from here never reaches the "
        "tenant's login page and disappears on every 401."
    )


# ---------------------------------------------------------------------------
# 7. The COMPANY NAME leaks -- found by looking at a screenshot, not by a test
#
# MEASURED 2026-08-19. With the colours and typeface finally correct, a
# screenshot of the branded acme dashboard still read:
#
#     "Overview of Dogar Trading Corporation"
#
# directly under a red, Georgia-set heading that said "Good evening, Acme
# Admin". Two more of the same kind were next to it: the footer's copyright
# holder, and the browser tab title (written on EVERY route change, so it also
# reached bookmarks, the window switcher and printed page headers).
#
# `brandFullName()` already existed and already fell back to the Dogar name, so
# each of these was a one-line omission rather than a missing capability -- and
# each was individually invisible, because the surrounding branding was right.
#
# This is why the section exists rather than a single "no hardcoded names"
# assertion: the FALLBACK strings inside branding.ts are legitimate and must not
# be flagged, so the rule is about where the literal may appear, not whether it
# appears.
# ---------------------------------------------------------------------------

DASHBOARD_TSX = REPO_ROOT / "app" / "frontend" / "src" / "pages" / "Dashboard.tsx"
BREADCRUMBS_TSX = REPO_ROOT / "app" / "frontend" / "src" / "layout" / "Breadcrumbs.tsx"

# Modules allowed to contain the literal company name in CODE:
#   branding.ts  -- it owns the fallback used when no tenant is resolved
#   MiscPages.tsx -- the Portal Admin screen explains, in prose to a root
#                    administrator, that editing a tenant does not overwrite the
#                    root Dogar portal. That sentence is ABOUT Dogar.
_NAME_LITERAL_OWNERS = {"branding.ts", "MiscPages.tsx"}

_ROOT_NAME = "Dogar Trading Corporation"


def _code_lines(path: Path) -> list[str]:
    """Source lines with comments removed.

    Block comments are stripped, not just `//` lines: the explanatory notes
    added alongside these fixes are JSX comments (`{/* ... */}`) whose middle
    lines start with ordinary prose, and they quote the very string the tests
    below forbid. Measuring the documentation instead of the program is a
    mistake this file has already made once (see section 6).
    """
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)      # /* ... */ and {/* ... */}
    return [ln for ln in text.splitlines() if not _is_comment_line(ln)]


def _unguarded_name_literals(path: Path) -> list[str]:
    """Lines holding the root company name WITHOUT a tenant lookup beside it.

    REFINED 2026-08-19 after the first version of this sweep reported four
    "leaks" that were all correct code:

        const brandName = TENANT_INFO?.name || 'Dogar Trading Corporation'

    That is a tenant-aware read with the root name as its FALLBACK -- exactly
    the required behaviour, just written inline instead of calling
    brandFullName(). Failing it would have pressured the next author into
    "fixing" working code.

    So the invariant is not "the literal never appears"; it is "the literal
    never appears as the ONLY thing on offer". A line that also mentions
    TENANT_INFO or brandFullName is resolving the office first and falling back
    second, which is the contract.
    """
    out = []
    for ln in _code_lines(path):
        if _ROOT_NAME not in ln:
            continue
        if "TENANT_INFO" in ln or "brandFullName" in ln:
            continue
        out.append(ln.strip())
    return out


@pytest.mark.parametrize(
    "path",
    [DASHBOARD_TSX, BREADCRUMBS_TSX, APP_LAYOUT_TSX],
    ids=["dashboard-subtitle", "browser-tab-title", "footer-copyright"],
)
def test_the_company_name_is_not_hardcoded_in_tenant_facing_chrome(path: Path):
    """Each of the three leaks the screenshot exposed, pinned separately.

    Parametrised rather than looped so a regression names the exact surface
    that broke instead of reporting one opaque failure for all three.
    """
    offenders = _unguarded_name_literals(path)
    assert not offenders, (
        f"{path.name} hardcodes the root company name in code: {offenders}. "
        "Use brandFullName(TENANT_INFO), which falls back to the Dogar name "
        "when no tenant is resolved -- so the root portal is unchanged while a "
        "partner office sees its own name."
    )


@pytest.mark.parametrize(
    "path",
    [DASHBOARD_TSX, BREADCRUMBS_TSX, APP_LAYOUT_TSX],
    ids=["dashboard-subtitle", "browser-tab-title", "footer-copyright"],
)
def test_each_fixed_surface_actually_resolves_the_name(path: Path):
    """Removing the literal is not the same as showing the right name.

    A surface could have been "fixed" by deleting the company name entirely,
    which passes the test above while quietly dropping branding the offices
    want. This asserts the replacement is wired in.
    """
    src = "\n".join(_code_lines(path))
    assert "brandFullName(" in src, (
        f"{path.name} no longer resolves the office name through "
        "brandFullName(); the surface may have been blanked instead of branded"
    )


def test_the_name_helper_still_falls_back_to_dogar():
    """The fallback is what keeps the root portal unchanged.

    Every fix in this section relies on brandFullName() returning the Dogar name
    for a null tenant. If that ever changed, all three surfaces would silently
    go blank on the root portal at once.
    """
    src = BRANDING_TS.read_text(encoding="utf-8")
    m = re.search(r"export function brandFullName\((.*?)\n\}", src, re.S)
    assert m, "brandFullName is no longer declared in a parseable form"
    assert "Dogar Trading Corporation" in m.group(1), (
        "brandFullName no longer falls back to the root company name, so the "
        "Dogar portal's own identity would disappear from the dashboard "
        "subtitle, the tab title and the footer simultaneously"
    )


def test_no_other_tenant_facing_page_hardcodes_the_company_name():
    """A sweep, so the next leak is caught where it is introduced.

    The three surfaces above were found one screenshot at a time. This asserts
    the whole SPA is clean, with an explicit allow-list of the two modules that
    legitimately own the literal -- so adding a fourth leak fails here instead
    of waiting for someone to notice it in an image.
    """
    src_dir = REPO_ROOT / "app" / "frontend" / "src"
    leaks: dict[str, list[str]] = {}
    for path in sorted(src_dir.rglob("*.tsx")) + sorted(src_dir.rglob("*.ts")):
        if path.name in _NAME_LITERAL_OWNERS:
            continue
        hits = _unguarded_name_literals(path)
        if hits:
            leaks[path.name] = hits
    assert not leaks, (
        f"the root company name is hardcoded in tenant-facing code: {leaks}. "
        "Every office-visible surface must resolve its name through "
        "brandFullName(TENANT_INFO)."
    )


def test_the_unauthenticated_tab_title_follows_the_office():
    """The fourth name leak, and the one furthest from any test.

    MEASURED in Chromium 2026-08-19: /t/acme/login had
    ``document.title === "Dogar Trading Corporation"`` while its colours and
    typeface were already correctly Acme's. index.html ships that title
    statically, and the only code that rewrote it was Breadcrumbs -- which,
    exactly like the old branding call, lives inside the authenticated shell.

    So the fix belongs at boot, next to the branding, and it must be GUARDED on
    TENANT_INFO: on the root portal the helper returns the same string the HTML
    already contains, and writing it anyway would make "Dogar is unchanged" a
    coincidence rather than a property.
    """
    src = "\n".join(
        "" if _is_comment_line(ln) else ln
        for ln in MAIN_TSX.read_text(encoding="utf-8").splitlines()
    )
    m = re.search(r"if\s*\(TENANT_INFO\)\s*document\.title\s*=\s*brandFullName\(", src)
    assert m, (
        "main.tsx does not set the document title from the active office. The "
        "static <title> in index.html names the root company, so an office's "
        "login tab and any bookmark made from it are labelled 'Dogar Trading "
        "Corporation'."
    )
    # It must not run before the tenant is known, and must not outrank the
    # per-page title the shell sets once mounted.
    assert src.index("applyTenantBranding(TENANT_INFO)") < m.start(), (
        "the title is written before the branding is applied; keep the two "
        "boot-time branding writes together and in a predictable order"
    )
    assert m.start() < src.index("createRoot"), (
        "the title is written after React renders, so the office's login tab "
        "would flash the root company's name first"
    )


def test_the_static_html_title_is_still_the_root_companys():
    """The other half of the same contract.

    The boot-time write is deliberately conditional, which only keeps the root
    portal correct while the static title IS the root company's name. If that
    ever changed, the Dogar portal would silently show whatever the new literal
    was, and nothing else in the suite would notice.
    """
    html = (REPO_ROOT / "app" / "frontend" / "index.html").read_text(encoding="utf-8")
    m = re.search(r"<title>([^<]*)</title>", html)
    assert m, "index.html no longer declares a <title>"
    assert m.group(1).strip() == "Dogar Trading Corporation", (
        f"the static <title> is {m.group(1)!r}. The boot-time title write is "
        "skipped on the root portal precisely because the HTML already carries "
        "the correct root name; changing it here changes the Dogar portal."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 8. THE UNAUTHENTICATED HERO ART AND THE PRIMARY ACTION
#
# WHY THIS SECTION EXISTS (2026-08-20)
# ------------------------------------
# By the time it was written, this feature had FORTY-NINE green source tests, a
# twenty-mutation batch with nothing escaping, and a Chromium harness reporting
# PASS on four surfaces -- correct typeface, correct accent token, correct tab
# title, no leaked company name. And a screenshot of /t/acme/login showed a
# partner office's staff:
#
#   * a full-height PHOTOGRAPH OF DOGAR'S OWN LOGO WALL as the page's hero,
#   * a second photo of the same office, blurred, behind the sign-in card,
#   * a DOGAR-BLUE "Sign In" button -- the single most prominent control on the
#     page -- next to a Dogar-blue "SECURE SIGN IN" chip, "Forgot password?"
#     and "Contact Support" links,
#
# while the office's accent was #ff0000 and the token carrying it was correct.
#
# Nothing was broken in the branding mechanism. The defect was that fourteen
# accent surfaces across Login.tsx and AuthPages.tsx were painted by Tailwind
# CLASS utilities (`bg-blue-600`, `text-blue-600`, `focus:ring-blue-500`) and
# two more by hard-coded `url(...)` declarations in index.css. A class selector
# is (0,1,0) and cannot be re-skinned by any custom property, which is the same
# specificity trap that defeated `--brand-font` -- met a second time, in a
# second place, and again invisible to every check that existed.
#
# So this section pins the *shape* of the fix: the tokens exist, the CSS reads
# them, the CSS still falls back to Dogar's exact previous values, and the two
# auth pages contain no raw blue utilities for the next author to copy.
# ═══════════════════════════════════════════════════════════════════════════════

LOGIN_TSX = REPO_ROOT / "app" / "frontend" / "src" / "pages" / "Login.tsx"
AUTH_PAGES_TSX = REPO_ROOT / "app" / "frontend" / "src" / "pages" / "AuthPages.tsx"

#: The literal declarations that were in index.css before this fix. They are
#: reproduced here ON PURPOSE: "Dogar is unchanged" is only a real guarantee
#: while the var() fallback is byte-for-byte the old value, so the assertion has
#: to know what the old value was.
_DOGAR_HERO = "url('/static/img/login/office-left.jpg')"
_DOGAR_HERO_SOFT = "url('/static/img/login/office-right.jpg')"


def test_the_hero_tokens_are_written_by_the_single_branding_writer():
    """`--brand-hero` / `--brand-hero-soft` must be set where the rest are.

    Not in a component `useEffect`, and not in a second module: the entire
    reason the earlier defect existed for as long as it did is that branding had
    ONE writer in the wrong place. Adding a second writer for these two tokens
    would reintroduce that class of bug with a different symptom.
    """
    src = BRANDING_TS.read_text(encoding="utf-8")
    fn = src[src.index("export function applyTenantBranding"):]
    for token in ("--brand-hero", "--brand-hero-soft"):
        assert f"setProperty('{token}'" in fn, (
            f"{token} is not written by applyTenantBranding(). The auth pages read "
            f"it, so if nothing sets it the office silently keeps Dogar's hero art."
        )


def test_the_hero_tokens_are_only_written_for_a_resolved_tenant():
    """The root portal must never have these properties set at all.

    This is what makes "Dogar is unchanged" structural. index.css falls back to
    Dogar's own photographs via var(), and a fallback only fires when the
    property is ABSENT -- not when it is present and empty. So the writes have
    to sit after the existing `if (!root || !tenant) return null` guard.
    """
    src = BRANDING_TS.read_text(encoding="utf-8")
    fn = src[src.index("export function applyTenantBranding"):]
    guard = fn.index("if (!root || !tenant) return null")
    for token in ("--brand-hero", "--brand-hero-soft"):
        assert fn.index(f"setProperty('{token}'") > guard, (
            f"{token} is written before the no-tenant guard, so the Dogar root "
            f"portal would be themed by the tenant path"
        )


def _rule_body(selector: str) -> str:
    """The declaration block of the first rule whose selector list starts with
    `selector`, comments stripped.

    Rules are addressed BY SELECTOR rather than by scanning the whole file for a
    property, because "somewhere in index.css a declaration reads this token" is
    not the invariant. The invariant is that THIS element reads it. See the note
    on the test below: an existence-only check is exactly what let mutation BU
    escape.
    """
    text = re.sub(r"/\*.*?\*/", "", INDEX_CSS.read_text(encoding="utf-8"), flags=re.S)
    m = re.search(re.escape(selector) + r"[^{]*\{([^}]*)\}", text)
    assert m, f"no rule for {selector!r} in index.css"
    return m.group(1)


@pytest.mark.parametrize(
    "selector, token, dogar_fallback",
    [
        # Each auth surface is named individually. The desktop hero and the
        # blurred right-hand panel are separate elements with separate
        # declarations, and the phone banner (asserted in its own test below) is
        # a third.
        (".login-page .panel-left", "--brand-hero", _DOGAR_HERO),
        (".login-page .panel-right", "--brand-hero-soft", _DOGAR_HERO_SOFT),
    ],
)
def test_the_auth_panels_read_the_token_and_still_fall_back_to_dogar(
    selector, token, dogar_fallback
):
    """Every auth-panel background must be `var(TOKEN, <the old value>)`.

    Both halves matter, and they fail in opposite directions:
      * without the var(), the office keeps Dogar's photograph -- the reported
        defect;
      * without the fallback, the DOGAR portal loses its own photograph, which
        would be a regression on the flagship portal introduced by a fix aimed
        at the tenants.

    REWRITTEN 2026-08-20 BECAUSE A MUTATION ESCAPED IT
    -------------------------------------------------
    The first version of this test asked "does index.css contain a declaration
    that reads this token, and if so does it carry the fallback?". Mutation BU
    reverted `.panel-left` -- the full-height hero, the actual reported defect,
    the largest thing on the page -- to its hard-coded `url(...)`, and this test
    still passed, because `.mobile-banner` also reads `--brand-hero` and one
    surviving declaration satisfied the existence check.

    That is the difference between a test and a coincidence. A file-wide
    existence check cannot express a per-element invariant: it goes green as long
    as ANY element is right, which is precisely the state a partial regression
    leaves the file in. So the rule is now addressed by SELECTOR and the
    assertion is made about that element's own declaration block.
    """
    body = _rule_body(selector)
    m = re.search(r"background-image\s*:\s*([^;]+);", body)
    assert m, (
        f"`{selector}` declares no background-image at all; the hero surface is "
        f"unpainted rather than merely mis-branded"
    )
    decl = m.group(1).strip()
    # The trailing comma anchors the token exactly: `--brand-hero` is a strict
    # PREFIX of `--brand-hero-soft`, and a plain substring test matched the wrong
    # panel's declaration -- a test failing for a reason with nothing to do with
    # the behaviour under test, which is worse than no test at all.
    assert f"var({token}," in decl, (
        f"`{selector}` paints `{decl}` instead of reading {token}. Every office's "
        f"portal therefore shows the ROOT company's photograph on this surface."
    )
    assert dogar_fallback in decl, (
        f"`{selector}` reads {token} but does not fall back to {dogar_fallback}. "
        f"On the root portal the property is never set, so this declaration "
        f"would resolve to nothing and the Dogar login page would lose its "
        f"hero art."
    )


def test_the_mobile_banner_hero_is_tenant_aware_too():
    """Below `lg` the left panel is display:none and this banner IS the hero.

    Fixing only `.panel-left` would have fixed the defect on desktop and left
    every phone visitor looking at another company's office -- a "fix" that
    passes a 1440px harness and is wrong for the majority of real traffic.
    """
    text = re.sub(r"/\*.*?\*/", "", INDEX_CSS.read_text(encoding="utf-8"), flags=re.S)
    m = re.search(r"\.login-page \.mobile-banner,.*?\}", text, flags=re.S)
    assert m, "the .mobile-banner rule is gone from index.css"
    assert "var(--brand-hero" in m.group(0), (
        "the mobile auth banner still hard-codes a background image, so phones "
        "get the root company's office photograph on every tenant portal"
    )


def test_the_primary_action_paints_from_the_accent_tokens():
    """`.btn-signin` is the most prominent control on the page.

    Also asserts the FOREGROUND comes from `--brand-on-solid` rather than a
    `text-white` utility. The accent is arbitrary tenant input: on a pale
    accent, white-on-white is an invisible label, and readableOn() already
    computed which ink clears WCAG AA. Hardcoding white throws that away.
    """
    text = re.sub(r"/\*.*?\*/", "", INDEX_CSS.read_text(encoding="utf-8"), flags=re.S)
    m = re.search(r"\.login-page \.btn-signin,.*?\}", text, flags=re.S)
    assert m, "the .btn-signin rule is gone from index.css"
    rule = m.group(0)
    assert "var(--brand-solid)" in rule, (
        "the Sign In button does not read --brand-solid; every office's login "
        "page shows the root portal's blue on its primary action"
    )
    assert "var(--brand-on-solid)" in rule, (
        "the Sign In button's label colour is not the WCAG-checked ink"
    )
    assert not re.search(r"#(2563eb|1d4ed8|3b82f6)", rule, re.I), (
        f"a literal Dogar blue survives in the button rule: {rule!r}"
    )


@pytest.mark.parametrize("path", [LOGIN_TSX, AUTH_PAGES_TSX])
def test_the_auth_pages_contain_no_raw_blue_utilities(path):
    """The call-site half of the fix.

    A `text-blue-600` in the markup is unreachable by any token write, so this
    is not a style preference -- it is the difference between a surface that
    follows the office and one that cannot. Comments are stripped so the note in
    Login.tsx that EXPLAINS the trap (and necessarily quotes `bg-blue-600`) does
    not fail the test that enforces it.
    """
    text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    hits = re.findall(r"[\w:\[\]-]*\b(?:bg|text|border|ring|from|to)-blue-\d{2,3}", text)
    assert not hits, (
        f"{path.name} still paints accent surfaces with Tailwind blue utilities: "
        f"{sorted(set(hits))}. These are class selectors (0,1,0) and no custom "
        f"property can re-skin them, so they stay Dogar blue on every office's "
        f"portal. Use .auth-accent / .auth-accent-tile / .auth-accent-input."
    )


@pytest.mark.parametrize(
    "cls", ["auth-accent", "auth-accent-tile", "auth-accent-tile-soft", "auth-accent-input"]
)
def test_every_auth_accent_helper_is_defined_and_token_driven(cls):
    """A class the markup uses but the stylesheet never defines renders as
    nothing at all -- and, being invisible rather than wrong-coloured, would be
    even harder to notice than the blue it replaced."""
    text = re.sub(r"/\*.*?\*/", "", INDEX_CSS.read_text(encoding="utf-8"), flags=re.S)
    m = re.search(rf"\.login-page \.{cls},.*?\}}", text, flags=re.S)
    assert m, f".{cls} is used by the auth pages but not defined in index.css"
    assert "var(--brand" in m.group(0), (
        f".{cls} does not read a brand token, so it cannot follow the office"
    )


def test_the_hero_helpers_derive_from_the_palette_not_from_a_raw_hex():
    """The gradients must be built from the DERIVED palette.

    `derivePalette()` is where the WCAG work and the tint/rule mixes live. A
    helper that interpolated `tenant.color` directly would bypass all of it and
    would also happily interpolate unparseable junk from a legacy row straight
    into a live stylesheet property.
    """
    src = BRANDING_TS.read_text(encoding="utf-8")
    for fn_name in ("heroBackground", "heroSoftBackground"):
        m = re.search(rf"export function {fn_name}\([^)]*\)[^{{]*{{(.*?)\n}}", src, re.S)
        assert m, f"{fn_name} is not exported from branding.ts"
        body = m.group(1)
        assert "palette." in body, f"{fn_name} does not read the derived palette"
        assert not re.search(r"#[0-9a-f]{6}", body.replace("#ffffff", ""), re.I), (
            f"{fn_name} interpolates a literal colour: {body.strip()!r}"
        )


# ---------------------------------------------------------------------------
# SECTION: the LETTERHEAD half of document branding (2026-08-20)
#
# The brief said "logo/colors/fonts". The font work above closed the font
# chain, and the palette work closed colour -- so document branding was
# recorded as delivered. It was half delivered, and the missing half was the
# one that puts the WRONG COMPANY'S IDENTITY on a legal document.
#
# `letterhead_url_for(tenant)` existed, was correct, and was called by exactly
# ONE of the nine renderers that draw the letterhead artwork. The other eight
# interpolated the module-level `LETTERHEAD_URL` -- Dogar's own scan, complete
# with Dogar Trading's licence number and postal address. So a partner office
# could upload its letterhead through POST /api/tenants/{id}/letterhead, get a
# 200, see the asset stored and served, and then print its visa undertaking on
# Dogar's stationery. The font and the accent colour were correctly its own,
# which made the result MORE convincing, not less.
#
# Nothing failed. There was no exception, no 500, no log line: the renderers
# were asking for a constant and getting it. Only a test that renders a
# document for a tenant and inspects the artwork could have seen it, and no
# such test existed -- the branding suite asserted on fonts and CSS variables,
# and the letterhead suite asserted on Dogar's own output.
#
# These tests therefore assert on RENDERED DOCUMENT OUTPUT, per renderer, and
# the "Dogar unchanged" case is asserted just as hard as the tenant case,
# because the brief's hard constraint is that the Dogar tenant does not move.
# ---------------------------------------------------------------------------

# The probe letterhead. It must be a real file on disk, because the whole point
# is to drive `_tenant_letterhead_data_url` -- the same code path an operator's
# upload exercises -- rather than to mock it.
#
# IT IS CREATED BY THIS MODULE, NOT FOUND. The first version pointed at
# `data/tenants/acme/logo_87deb038.webp`, a file that happened to exist in the
# development workspace. `data/` is git-ignored and excluded from the release
# archive, so the moment these tests ran against the PACKAGED build -- which is
# exactly the release-candidate verification the brief requires -- eight of them
# failed with "letterhead_url_for() ignored the active office, so the eight
# renderers that pass no tenant are back to printing Dogar's stationery for
# everyone". The product was perfectly fine; the tests were reading local
# sediment. A verification that accuses the product of a defect it does not have
# is worse than no verification, and this is the second time that exact shape
# has appeared in this project (the acme/beta tenant fixtures were the first).
#
# So the asset is now a fixture the suite owns: written to a temp dir under the
# renderer's own assets root on first use, cleaned up at session end, and
# byte-distinct from Dogar's letterhead by construction.
_PROBE_SLUG = "_probe_office"
_PROBE_ASSET = "probe_letterhead.png"

# A 1x1 opaque PNG. Tiny, valid, and -- critically -- not Dogar's scan, so
# `letterhead_url_for` returning it can never be confused with the fallback.
_PROBE_PNG_BYTES = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAF"
    b"hQI/2Q5tAAAAAElFTkSuQmCC"
)


@pytest.fixture(scope="session", autouse=True)
def _probe_letterhead_on_disk():
    """Materialise the probe letterhead for the whole session.

    Session-scoped and autouse so no individual test can forget it, and so the
    file exists before `_tenant_letterhead_data_url` (which is lru_cached) is
    first consulted. The cache is cleared on both sides: a stale empty-string
    entry from an earlier lookup would otherwise pin the Dogar fallback for the
    rest of the run.
    """
    from app.services import letterhead_renderer as _lr

    root = REPO_ROOT / _lr._TENANT_ASSETS_ROOT / _PROBE_SLUG
    path = root / _PROBE_ASSET
    created_dir = not root.exists()
    root.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PROBE_PNG_BYTES)
    _lr._tenant_letterhead_data_url.cache_clear()
    try:
        yield path
    finally:
        _lr._tenant_letterhead_data_url.cache_clear()
        try:
            path.unlink()
            if created_dir:
                root.rmdir()
        except OSError:
            # Leaving a 70-byte probe file behind must never fail a test run.
            pass


class _StubTenant:
    """Minimal stand-in for a ResolvedTenant / Tenant row.

    Deliberately NOT a real ORM object: these tests must exercise the branding
    contract, not the database, so they stay fast and cannot be made to pass by
    seeding a fixture.
    """

    def __init__(self, slug=_PROBE_SLUG, asset=_PROBE_ASSET, font_key="serif"):
        self.slug = slug
        self.letterhead_path = asset
        self.letterhead_filename = None
        self.font_key = font_key


def _renderers_that_draw_letterhead():
    """Every renderer whose template interpolates the letterhead artwork.

    Discovered from the SOURCE rather than listed by hand. A hand-written list
    is how the original defect survived: eight call sites existed and the
    feature was signed off against the one that had been updated. If a tenth
    renderer is added tomorrow it is covered the day it is written.

    THE PREDICATE MATTERS, AND THE FIRST ONE WAS WRONG. It required
    ``letterhead_url_for`` to appear in the body -- i.e. it selected renderers
    that were ALREADY FIXED. Verified by reintroducing the defect in one
    renderer: discovery fell from 9 to 8, the offender quietly left the
    parametrisation, and the eight behavioural tests all passed. The check
    would have been strongest exactly when there was nothing wrong and blind
    the moment something broke. That is the same self-disabling failure that
    has now bitten four mutation anchors in this project.

    So the predicate keys off the ARTWORK SLOT (`<img class="bg" src=...>`),
    which is present whether the renderer is right or wrong. A regressed
    renderer therefore stays in the list and FAILS, which is the entire point.
    """
    import ast

    src_path = (REPO_ROOT / "app" / "services" / "letterhead_renderer.py")
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines()
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not node.name.startswith("render_"):
            continue
        body = "\n".join(lines[node.lineno - 1:(node.end_lineno or node.lineno)])
        if 'class="bg"' in body and 'src="{' in body:
            found.append(node.name)
    return sorted(found)


def test_the_probe_asset_exists():
    """Guard the guard.

    If the probe letterhead is not on disk, `letterhead_url_for` falls back to
    Dogar's -- and every assertion below would compare Dogar's artwork to
    Dogar's artwork and PASS while proving nothing. That is the vacuous-check
    failure mode, so absence is a hard error, not a skip.

    The `_probe_letterhead_on_disk` fixture is what puts it there, so this test
    also proves that fixture actually ran. It is autouse and session-scoped
    precisely so this cannot depend on test ordering.
    """
    from app.services.letterhead_renderer import _TENANT_ASSETS_ROOT

    path = REPO_ROOT / _TENANT_ASSETS_ROOT / _PROBE_SLUG / _PROBE_ASSET
    assert path.is_file(), (
        f"probe asset {path} is missing, so these tests would silently measure "
        f"the Dogar fallback against itself and pass vacuously. It is created by "
        f"the _probe_letterhead_on_disk fixture -- if it is absent, that fixture "
        f"did not run or could not write to the assets root."
    )


def test_the_tenant_letterhead_differs_from_dogars_at_all():
    """The precondition every other test here depends on."""
    from app.services.letterhead_renderer import LETTERHEAD_URL, letterhead_url_for

    url = letterhead_url_for(_StubTenant())
    assert url != LETTERHEAD_URL, (
        "the tenant's letterhead resolved to Dogar's, so nothing below can "
        "distinguish branded output from unbranded output"
    )
    # MIME derived from the probe's own extension, not hard-coded: pinning
    # "webp" here is what tied this assertion to the specific untracked file
    # that used to live in the dev workspace.
    expected_mime = {"png": "image/png", "webp": "image/webp",
                     "jpg": "image/jpeg", "jpeg": "image/jpeg"}[
        _PROBE_ASSET.rsplit(".", 1)[-1].lower()]
    assert url.startswith(f"data:{expected_mime};base64,"), url[:40]


def test_no_renderer_hardcodes_dogars_letterhead():
    """Structural companion: the artwork must come from the resolver.

    The behavioural tests below prove the current renderers are correct. This
    one prevents the ORIGINAL mistake from being reintroduced in a new
    renderer, where a behavioural test that nobody wrote cannot catch it.
    """
    src = (REPO_ROOT / "app" / "services" / "letterhead_renderer.py").read_text(
        encoding="utf-8")
    offenders = re.findall(r'src="\{LETTERHEAD_URL\}"', src)
    assert not offenders, (
        f"{len(offenders)} renderer(s) still interpolate the module-level "
        f"LETTERHEAD_URL into an <img src>. That constant is Dogar's own scan, "
        f"so those documents print a partner office's letter on Dogar Trading's "
        f"stationery. Use letterhead_url_for() instead."
    )


def test_every_letterhead_renderer_was_found():
    """The discovery helper must actually be discovering something.

    An AST walk that silently matches nothing would make the parametrised test
    below collect zero cases and report success.
    """
    names = _renderers_that_draw_letterhead()
    assert len(names) >= 8, (
        f"expected the letterhead artwork in at least 8 renderers, found "
        f"{len(names)}: {names}. Either the template changed shape or this "
        f"helper has stopped matching -- both make the coverage below vacuous."
    )


@pytest.mark.parametrize("fn_name", _renderers_that_draw_letterhead())
def test_each_renderer_draws_the_active_offices_letterhead(fn_name):
    """EVERY renderer, not a sampled one.

    The defect was uneven: one renderer was right and eight were wrong. A test
    that checked a single document would have had a 1-in-9 chance of finding
    it, and would have reported the feature working.
    """
    import app.services.letterhead_renderer as lr
    from app.core.branding_contract import current_tenant

    tenant = _StubTenant()
    tenant_art = lr.letterhead_url_for(tenant)[:64]
    dogar_art = lr.LETTERHEAD_URL[:64]

    fn = getattr(lr, fn_name)
    args = _probe_args_for(fn_name)

    token = current_tenant.set(tenant)
    try:
        html = fn(*args, auto_print=False)
    finally:
        current_tenant.reset(token)

    assert tenant_art in html, (
        f"{fn_name} did not draw the active office's letterhead; the office's "
        f"document is carrying someone else's identity"
    )
    assert dogar_art not in html, (
        f"{fn_name} still embeds Dogar Trading's letterhead while rendering for "
        f"tenant {tenant.slug!r} -- licence number and address included"
    )


@pytest.mark.parametrize("fn_name", _renderers_that_draw_letterhead())
def test_each_renderer_leaves_dogars_own_documents_untouched(fn_name):
    """The brief's hard constraint, asserted per renderer.

    Making tenant branding work is worthless if it moves Dogar's own output.
    With no active tenant the resolver must return Dogar's artwork byte-for-
    byte, so this asserts the ABSENCE of drift rather than trusting the
    fallback to stay correct.
    """
    import app.services.letterhead_renderer as lr

    fn = getattr(lr, fn_name)
    html = fn(*_probe_args_for(fn_name), auto_print=False)
    assert lr.LETTERHEAD_URL[:64] in html, (
        f"{fn_name} no longer draws Dogar's letterhead on the root portal -- "
        f"tenant branding has regressed the unbranded path"
    )


def test_an_office_without_an_uploaded_letterhead_still_gets_a_document():
    """Graceful degradation, not a blank sheet.

    A new office has branding set but no letterhead uploaded yet. The resolver
    must fall back to a usable image rather than emitting an empty src, because
    `<img src="">` re-requests the current page and the sheet prints blank --
    a failure the operator would see only on paper.
    """
    from app.services.letterhead_renderer import LETTERHEAD_URL, letterhead_url_for

    bare = _StubTenant(asset=None)
    assert letterhead_url_for(bare) == LETTERHEAD_URL


def test_letterhead_resolution_falls_back_to_the_active_office():
    """`None` must mean "the active office", not "Dogar".

    This is the fix that made the eight parameterless renderers reachable at
    all. If `letterhead_url_for()` reverted to returning Dogar unconditionally,
    every renderer would compile, every existing test would pass, and the
    original defect would be back.
    """
    from app.core.branding_contract import current_tenant
    from app.services.letterhead_renderer import LETTERHEAD_URL, letterhead_url_for

    assert letterhead_url_for() == LETTERHEAD_URL, "no tenant must mean Dogar"

    token = current_tenant.set(_StubTenant())
    try:
        assert letterhead_url_for() != LETTERHEAD_URL, (
            "letterhead_url_for() ignored the active office, so the eight "
            "renderers that pass no tenant are back to printing Dogar's "
            "stationery for everyone"
        )
    finally:
        current_tenant.reset(token)


def test_an_explicit_tenant_still_overrides_the_active_one():
    """The one renderer that threads `tenant=` must keep winning.

    Same precedence rule as `document_font_stack`, so the two halves of
    document branding cannot disagree about whose document is being printed.
    """
    from app.core.branding_contract import current_tenant
    from app.services.letterhead_renderer import letterhead_url_for

    explicit = _StubTenant(asset=None)          # resolves to Dogar
    ambient = _StubTenant()                     # resolves to acme artwork
    token = current_tenant.set(ambient)
    try:
        assert letterhead_url_for(explicit) == letterhead_url_for(
            _StubTenant(asset=None)), "explicit argument lost to the ContextVar"
    finally:
        current_tenant.reset(token)


def _probe_args_for(fn_name: str):
    """Positional arguments for a renderer, by shape.

    The renderers take either an iterable of candidates or a single demand.
    Stubs rather than ORM rows: these tests are about which ARTWORK is
    embedded, and a database fixture would add a way for them to fail for
    reasons unrelated to branding.
    """
    class _Demand:
        id = 1
        company_name = "Probe Co"
        country = "Saudi Arabia"
        embassy = "Islamabad"
        demand_number = "D-1"
        visa_number = "V-1"
        profession = "Driver"
        quantity = 1

    class _Cand:
        id = 1
        name = "Probe Candidate"
        father_name = "Probe Father"
        passport_no = "AB1234567"
        profession = "Driver"
        visa_number = "V-1"

    if "demand" in fn_name:
        return (_Demand(),)
    return ([_Cand()],)
