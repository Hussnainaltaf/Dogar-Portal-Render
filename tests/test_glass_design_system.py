"""The light-glassmorphism layer (app.css v19) must stay safe and complete.

WHY THIS FILE EXISTS
--------------------
The 2026-08-16 direction change asked for "a fully light, glassmorphism-based
SaaS interface across the entire frontend, with translucent surfaces, soft
depth, dynamic page composition, and more advanced modern controls while
preserving the existing workflow, branding identity, API behavior, RBAC, audit
logging, and tenant isolation."

A glass redesign is unusually easy to ship BROKEN in two specific ways, and
both have already bitten this project once:

1. `backdrop-filter` establishes a CONTAINING BLOCK for `position: fixed`
   descendants -- exactly like the `transform` that `.page-enter`'s
   `animation-fill-mode: both` left asserted and which clipped the Customize
   Role dialog mid-form with its Save button unreachable (fixed 2026-08-13,
   guarded by tests/test_fixed_overlay_containing_block.py).

   A glass pass sprays `backdrop-filter` across the app. If it lands on
   <html>, <body>, or the routed content wrapper, EVERY non-portalled overlay
   in the app is clipped again -- and the visual result looks plausible, so it
   would ship. These tests assert the blur stays on leaf surfaces.

2. Translucency without a fallback is an accessibility regression. On a
   browser with no `backdrop-filter` support the surfaces stay semi
   transparent but UNBLURRED, so body text sits directly on the wallpaper
   gradient. Users who set `prefers-reduced-transparency` have asked the
   operating system not to do this to them at all.

The tests below therefore check the CONTRACT of the layer -- that it is
present, token-driven, safe, and reversible -- rather than pinning individual
colour values, which are a design decision and are expected to be retuned.
"""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(REPO_ROOT, "app", "static", "css", "app.css")
SPA_HTML = os.path.join(REPO_ROOT, "app", "frontend", "index.html")

MARKER = "LIGHT GLASSMORPHISM LAYER (v19)"


def _css() -> str:
    with open(CSS, encoding="utf-8") as fh:
        return fh.read()


def _v19() -> str:
    """Only the appended v19 layer, so earlier layers are not re-litigated."""
    css = _css()
    assert MARKER in css, (
        f"the {MARKER!r} block is gone from app/static/css/app.css. The light "
        "glassmorphism interface was an explicit product direction; if it was "
        "intentionally reverted, delete this test file in the same commit so "
        "the suite states the truth."
    )
    return css.split(MARKER, 1)[1]


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


# ---------------------------------------------------------------------------
# 1. The layer exists and is token-driven
# ---------------------------------------------------------------------------

def test_glass_layer_is_present():
    assert MARKER in _css()


def test_glass_surface_ramp_is_defined_as_tokens():
    """Hard-coded rgba() on every rule would make the theme untunable and
    would not follow `.theme-forest-green`."""
    body = _strip_comments(_v19())
    for token in ("--glass-1", "--glass-2", "--glass-3", "--glass-4"):
        assert re.search(rf"{token}\s*:", body), (
            f"the glass surface ramp lost {token}. The four steps (recessed "
            "well / card / popover / modal) are what stop the UI reading as "
            "one flat sheet of milk."
        )


def test_layered_depth_tokens_are_defined():
    """Glass depth needs stacked shadows plus an inset rim; a single blurred
    shadow reads as a grey smudge."""
    body = _strip_comments(_v19())
    for token in ("--depth-1", "--depth-2", "--depth-3", "--depth-4"):
        assert re.search(rf"{token}\s*:", body), f"missing depth token {token}"
    # The rim highlight is what makes a translucent edge look like glass.
    assert "inset 0 1px 0" in body, (
        "no inset rim highlight in the depth tokens -- translucent surfaces "
        "lose their lit top edge and look like flat transparency."
    )


def test_blur_strengths_are_named_tokens():
    body = _strip_comments(_v19())
    for token in ("--blur-sm", "--blur-md", "--blur-lg"):
        assert re.search(rf"{token}\s*:", body), f"missing blur token {token}"


def test_forest_green_theme_still_reskins_the_canvas():
    """Tenant/user theming must survive the redesign."""
    body = _v19()
    assert ".theme-forest-green body" in body, (
        "the v19 canvas does not provide a `.theme-forest-green` variant, so "
        "the green theme would show a blue ambient wash."
    )


def test_glass_layer_uses_brand_token_not_hardcoded_brand_colour():
    """Branding identity must stay driven by --brand so a tenant re-skin still
    works end to end.

    Checked PER TOKEN, not once for the whole layer. An earlier version of this
    test only asserted that `var(--brand)` appeared *somewhere* in the layer;
    mutation KI hard-coded the accent washes to literal rgba() and the test
    still passed, because 12 other rules mentioned the token. A file-wide
    "contains" check is nearly worthless for this class of regression.
    """
    body = _strip_comments(_v19())

    # Every token whose whole purpose is to carry the brand hue.
    brand_derived = ("--accent-wash", "--accent-wash-2")
    for token in brand_derived:
        m = re.search(rf"{token}\s*:\s*([^;]+);", body)
        assert m, f"brand-derived token {token} is missing from the glass layer"
        value = m.group(1)
        assert "var(--brand" in value, (
            f"{token} is set to {value.strip()!r}, which hard-codes a colour "
            "instead of deriving it from var(--brand). Tenant branding and the "
            "forest-green theme would stop propagating to selected rows and "
            "active segments -- the UI would silently stay blue for a tenant "
            "whose brand is green."
        )


# ---------------------------------------------------------------------------
# 2. THE SAFETY CONTRACT -- this is the part that prevents an outage
# ---------------------------------------------------------------------------

# Any non-`none` value of these makes an element a containing block for
# `position: fixed` descendants.
CONTAINING_BLOCK_PROPS = ("will-change", "perspective")


def test_glass_layer_introduces_no_containing_block_properties():
    body = _strip_comments(_v19())
    for prop in CONTAINING_BLOCK_PROPS:
        hits = [
            ln.strip()
            for ln in body.splitlines()
            if re.search(rf"(^|[\s;]){prop}\s*:", ln)
        ]
        assert not hits, (
            f"the glass layer declares `{prop}`, which establishes a "
            f"containing block for fixed descendants and re-breaks every "
            f"overlay not portalled to <body> -- the 2026-08-13 dialog "
            f"clipping outage. Offending line(s): {hits}"
        )
    # `contain:` is checked separately so the word "contain" inside e.g.
    # `background-repeat` or prose cannot trigger a false positive.
    contain_hits = [
        ln.strip()
        for ln in body.splitlines()
        if re.search(r"(^|[\s;])contain\s*:", ln)
    ]
    assert not contain_hits, f"glass layer declares `contain`: {contain_hits}"


def _without_important(value: str) -> str:
    """Strip a trailing `!important` from a declaration value.

    This used to be `value.strip().rstrip("!important").strip()`, which ruff
    flagged as B005 -- and ruff was right that it is a real latent bug, not a
    style nit. `str.rstrip` takes a SET OF CHARACTERS, so that expression
    stripped any trailing run of `!`, `i`, `m`, `p`, `o`, `r`, `t`, `a`, `n`.

    MEASURED: the current inputs all happened to survive it -- `"none"` is
    left intact because rstrip halts at `o`... but only by luck of ordering,
    and `"inherit"` would be chewed down to `"inh"`, silently classifying a
    frosted rule as unfrosted. Anchored regex removal is exact.

    Lower-cased on the way out because CSS keywords are case-insensitive:
    `backdrop-filter: NONE` disables the blur just as `none` does, and the
    caller compares against the literal `"none"`.
    """
    return re.sub(r"\s*!\s*important\s*$", "", value.strip(),
                  flags=re.I).strip().lower()


def _selectors_carrying_backdrop_filter(text: str) -> list[str]:
    """Selectors of every rule in `text` that sets a non-none backdrop-filter."""
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        selector, decls = m.group(1), m.group(2)
        if not re.search(r"(?<!-)\bbackdrop-filter\s*:", decls):
            continue
        value = re.search(r"(?<!-)\bbackdrop-filter\s*:\s*([^;]+)", decls)
        if value and _without_important(value.group(1)) == "none":
            continue
        out.append(" ".join(selector.split()))
    return out


def test_backdrop_filter_is_never_applied_to_a_root_or_page_wrapper():
    """The single most dangerous thing a glass pass can do.

    Blurring html/body/#root/.page-enter/.spa-main-content would make them
    containing blocks, so `fixed inset-0` would resolve against the wrapper
    instead of the viewport and dialogs would be clipped inside the page.
    """
    forbidden = (
        "html",
        "body",
        "#root",
        ".page-enter",
        ".spa-main",
        ".spa-main-content",
    )
    offenders = []
    for selector in _selectors_carrying_backdrop_filter(_strip_comments(_v19())):
        # Split compound selectors so `.theme-forest-green body` is caught too.
        parts = re.split(r"[\s,>+~]+", selector)
        for bad in forbidden:
            if bad in parts:
                offenders.append(f"{selector!r} (matched {bad!r})")
    assert not offenders, (
        "backdrop-filter is applied to a root/page-wrapper element:\n  "
        + "\n  ".join(offenders)
        + "\n\nThat element becomes the containing block for every "
        "`position: fixed` descendant, so overlays stop filling the viewport. "
        "Blur LEAF surfaces (cards, inputs, chrome, portalled modal cards) "
        "instead."
    )


def test_body_gets_its_depth_from_a_background_not_a_filter():
    """The ambient wash must be a paint-only property.

    `background-image` does not create a containing block; `filter` and
    `backdrop-filter` do. This test pins the safe implementation so a future
    'make the wallpaper prettier' edit cannot quietly swap it.
    """
    body = _strip_comments(_v19())
    m = re.search(r"(?<![\w.#-])body\s*\{([^{}]*)\}", body)
    assert m, "the v19 layer no longer styles `body` for the ambient canvas"
    decls = m.group(1)
    assert "background-image" in decls, (
        "the ambient light canvas is gone; translucent surfaces have nothing "
        "to refract and the whole UI reads as flat pale grey."
    )
    assert not re.search(r"(^|[\s;])filter\s*:", decls), (
        "`filter` on <body> makes it a containing block for fixed overlays."
    )
    assert not re.search(r"(?<!-)\bbackdrop-filter\s*:", decls), (
        "`backdrop-filter` on <body> makes it a containing block for fixed "
        "overlays -- every dialog would be clipped to the body box."
    )


def test_every_real_backdrop_filter_has_a_webkit_prefixed_partner():
    """Safari/iOS still needs -webkit-backdrop-filter. Without it the surface
    stays translucent but UNBLURRED on iPhone, so text sits on raw wallpaper."""
    body = _strip_comments(_v19())
    missing = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", body):
        selector, decls = m.group(1), m.group(2)
        if not re.search(r"(?<!-)\bbackdrop-filter\s*:", decls):
            continue
        if "-webkit-backdrop-filter" not in decls:
            missing.append(" ".join(selector.split()))
    assert not missing, (
        "these rules set `backdrop-filter` with no `-webkit-` partner, so the "
        "glass silently degrades to unblurred transparency on Safari/iOS:\n  "
        + "\n  ".join(missing)
    )


# ---------------------------------------------------------------------------
# 3. Accessibility / capability fallbacks
# ---------------------------------------------------------------------------

def test_there_is_an_opaque_fallback_when_backdrop_filter_is_unsupported():
    body = _v19()
    assert "@supports not" in body and "backdrop-filter" in body, (
        "no `@supports not (backdrop-filter…)` fallback. On a browser without "
        "support the surfaces stay semi-transparent but unblurred, putting "
        "body text directly on the gradient wallpaper."
    )


def _at_rule_body(text: str, needle: str) -> str:
    """Return the body of the at-rule containing `needle`, matching braces.

    Written by brace counting rather than a regex sentinel on purpose. The
    first version of this helper cut the block at the literal string "\\n}\\n\\n",
    which does not occur after the reduced-transparency rule -- so the "block"
    silently ran to the end of the file and picked up the `backdrop-filter:
    none` belonging to the @media print rule further down. Mutation KE gutted
    the fallback and the test still passed. Brace matching cannot drift like
    that.
    """
    start = text.find(needle)
    assert start != -1, f"{needle!r} not found"
    open_brace = text.find("{", start)
    assert open_brace != -1, f"no opening brace after {needle!r}"
    depth = 0
    for i in range(open_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : i]
    raise AssertionError(f"unbalanced braces after {needle!r}")


def test_reduced_transparency_preference_is_honoured():
    body = _strip_comments(_v19())
    assert "prefers-reduced-transparency" in body, (
        "the layer ignores `prefers-reduced-transparency`. Users who asked "
        "their OS to reduce transparency must get opaque surfaces."
    )
    block = _at_rule_body(body, "prefers-reduced-transparency")
    assert "backdrop-filter: none" in block, (
        "the `prefers-reduced-transparency` block no longer disables the blur, "
        "so users who asked their OS for opaque UI still get frosted panels. "
        f"Block body was: {block.strip()[:400]!r}"
    )
    # The blur must be switched off, not merely re-declared at a lower value.
    assert not re.search(r"backdrop-filter:\s*(var\(--blur|blur\()", block), (
        "the reduced-transparency block re-applies a blur instead of removing "
        "it. `none` is the only acceptable value here."
    )


def test_reduced_motion_preference_disables_the_shimmer_and_lift():
    body = _v19()
    assert "prefers-reduced-motion" in body, (
        "the glass layer adds a skeleton shimmer and hover lifts but never "
        "honours `prefers-reduced-motion`."
    )


def test_print_path_still_forces_opaque_surfaces():
    """Printed embassy/bank paperwork must never render frosted.

    The print block lives EARLIER in app.css than the v19 layer, so this is a
    cascade check on the whole file, not just the new block: the print rules
    use `!important`, which beats later source order.
    """
    css = _strip_comments(_css())
    block = _at_rule_body(css, "@media print")

    # Every selector that the v19 layer frosts must be de-frosted for print.
    # Asserting a single occurrence is not enough: the print block resets the
    # blur in more than one rule, and mutation KF deleted one of them while a
    # count-blind "is the string present" check still passed. The regression it
    # models is real -- a frosted panel prints as a grey wash over bank and
    # embassy paperwork.
    frosted_for_print = (
        "body",
        ".card",
        "header.topbar",
        "aside#sidebar",
    )
    resets = {}
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
        selector, decls = " ".join(m.group(1).split()), m.group(2)
        if re.search(r"backdrop-filter:\s*none\s*!important", decls):
            for part in re.split(r"[\s,>+~]+", selector):
                resets[part] = True

    missing = [sel for sel in frosted_for_print if sel not in resets]
    assert not missing, (
        "the @media print block no longer forces `backdrop-filter: none "
        f"!important` for: {missing}. Those surfaces are frosted by the v19 "
        "layer, and because v19 is appended LATER in the file it wins on "
        "source order unless print uses !important -- so printed bank deposit "
        "slips and embassy forms would carry a grey frosted wash."
    )


# ---------------------------------------------------------------------------
# 4. Cache correctness -- a redesign nobody sees is not shipped
# ---------------------------------------------------------------------------

def test_spa_html_cache_bust_was_bumped_past_the_pre_glass_version():
    """app.css is served as a standalone static file, NOT bundled by Vite, so
    it gets no content hash in its filename. A returning browser would keep
    serving the pre-glass copy from disk cache unless the query string moves.
    """
    with open(SPA_HTML, encoding="utf-8") as fh:
        html = fh.read()
    m = re.search(r"/static/css/app\.css\?v=([\d.]+)", html)
    assert m, (
        "app/frontend/index.html no longer requests app.css with a `?v=` "
        "cache-busting query string; returning users would keep the stale "
        "stylesheet after a visual change."
    )
    assert float(m.group(1)) >= 23.0, (
        f"app.css cache-bust is v{m.group(1)}, but the glassmorphism layer "
        "shipped at v23.0. Returning browsers would serve the pre-glass "
        "stylesheet from cache."
    )


# ---------------------------------------------------------------------------
# 5. Preservation guarantees -- the redesign must be skin-deep
# ---------------------------------------------------------------------------

def test_glass_layer_is_purely_presentational():
    """A stylesheet cannot change RBAC or API behaviour, but it CAN hide
    controls, which is indistinguishable from a broken permission check to the
    user. `display: none` / `visibility: hidden` / `pointer-events: none` on a
    non-decorative selector is therefore banned from this layer.
    """
    body = _strip_comments(_v19())
    offenders = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", body):
        selector, decls = " ".join(m.group(1).split()), m.group(2)
        if re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", decls):
            offenders.append(f"{selector} (hides an element)")
        if re.search(r"pointer-events\s*:\s*none", decls):
            # Decorative pseudo-element overlays legitimately need this.
            if "::before" not in selector and "::after" not in selector:
                offenders.append(f"{selector} (disables interaction)")
    assert not offenders, (
        "the glass layer hides or disables interactive elements, which looks "
        "identical to a broken permission check from the user's side:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 6. Cascade correctness -- the layer must actually WIN where it applies
#
# Both tests below exist because of defects that shipped GREEN past every
# other test in this file. A CSS-only design layer has two silent failure
# modes that static assertions about app.css cannot see:
#
#   (a) a Tailwind utility with the same specificity beats it on SOURCE
#       ORDER, because index.html links app.css BEFORE the compiled bundle;
#   (b) a component never opts in to the layer's class at all, so the layer
#       is correct and complete and still reaches nothing.
#
# (a) was `bg-slate-50` on <body>, which held the canvas at rgb(248,250,252)
# while every app.css assertion passed. (b) was the Dashboard, which built
# its surfaces from `bg-white p-5 rounded-xl border border-slate-200
# shadow-sm` and therefore measured ZERO glass surfaces in
# scripts/verify_glass_ui.py while the other five pages measured 4-5.
# ---------------------------------------------------------------------------

# Utilities that would override a v19 surface declaration if they appear on a
# glass surface. Each maps to the property the glass layer sets.
OPAQUE_UTILITIES = {
    "bg-white":          "background: var(--glass-2)",
    "bg-slate-50":       "background: var(--glass-2)",
    "bg-gray-50":        "background: var(--glass-2)",
    "shadow-sm":         "box-shadow: var(--depth-2)",
    "border-slate-200":  "border-color: var(--glass-border)",
}

GLASS_PAGES = ("Dashboard.tsx",)


def _spa_html_body_tag() -> str:
    """The real opening <body> tag, with HTML comments removed first.

    MEASURED BUG in the first version of this helper: index.html discusses this
    very cascade trap in a comment at line 101 ("a Tailwind utility class on
    <body> sets background-color with ..."). A bare `<body\\b[^>]*>` search
    matched that PROSE, which carries no class attribute, so the assertion
    below inspected an empty class set and held trivially. A mutation that put
    `bg-slate-50` back on the real tag escaped, with all tests still green.
    Comments are therefore stripped before matching, and a `class=` attribute
    is required so the test can never again pass by finding the wrong thing.
    """
    with open(SPA_HTML, encoding="utf-8") as fh:
        html = fh.read()
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    m = re.search(r"<body\b[^>]*>", html)
    assert m, "app/frontend/index.html has no real <body> tag outside comments"
    tag = m.group(0)
    assert "class=" in tag, (
        f"the <body> tag {tag!r} has no class attribute. This test can only "
        "detect an overriding background utility by reading that attribute, "
        "so an absent one makes the check vacuous rather than passing."
    )
    return tag


def test_body_carries_no_background_utility_that_outranks_the_glass_canvas():
    """The ambient canvas is painted by a `body` type selector in app.css.

    app.css is linked BEFORE the compiled Tailwind bundle, so a single
    background utility on <body> -- one class, same specificity, later in
    source order -- wins outright. This is not theoretical: `bg-slate-50` was
    present and measured, holding the canvas at rgb(248,250,252) instead of
    the intended tinted gradient, with a card-vs-canvas delta of only 19.2.
    Removing it moved the measured canvas to rgb(232,237,246) and the delta
    to 26.8. Every other test in this file still passed throughout, which is
    precisely why this one exists.
    """
    tag = _spa_html_body_tag()
    cls = re.search(r'class="([^"]*)"', tag)
    classes = set(cls.group(1).split()) if cls else set()
    offenders = sorted(c for c in classes
                       if re.match(r"^bg-(?!transparent$)", c))
    assert not offenders, (
        f"<body> in app/frontend/index.html carries {offenders}, which "
        "overrides the app.css `body` background because app.css is linked "
        "earlier in the document than the compiled Tailwind bundle. The "
        "ambient glass canvas would silently not render."
    )


def test_glass_pages_do_not_rebuild_surfaces_from_opaque_utilities():
    """A page that hand-rolls its panels cannot receive a CSS-only layer.

    The Dashboard did exactly that and measured 0 glass surfaces while the
    other five pages measured 4-5. The fix was to opt those surfaces into
    `.card` AND delete the utilities, since keeping both would have let the
    utility win on source order.

    This asserts the combination that is actually broken -- an opaque utility
    sitting on an element that also claims a glass class -- plus the specific
    `bg-white` panel recipe that means a surface never opted in at all.
    """
    offenders = []
    for name in GLASS_PAGES:
        path = os.path.join(REPO_ROOT, "app", "frontend", "src", "pages", name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        for m in re.finditer(r'className="([^"]*)"', src):
            classes = set(m.group(1).split())
            line = src[:m.start()].count("\n") + 1
            glassy = classes & {"card", "card-elevated", "stat-tile",
                                "btn-secondary", "btn-outline"}
            clashes = sorted(classes & set(OPAQUE_UTILITIES))
            if glassy and clashes:
                offenders.append(
                    f"{name}:{line} has glass class {sorted(glassy)} AND "
                    f"{clashes}; the utility wins on source order, so "
                    + ", ".join(OPAQUE_UTILITIES[c] for c in clashes)
                    + " never applies"
                )
            elif "bg-white" in classes and "rounded-xl" in classes:
                offenders.append(
                    f"{name}:{line} builds an opaque panel from utilities "
                    "(`bg-white ... rounded-xl`) instead of `.card`, so the "
                    "v19 glass layer cannot reach it at all"
                )
    assert not offenders, (
        "glass-page surfaces are unreachable by the v19 layer:\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# 6. The glass layer must not erase longhands it does not own
# ---------------------------------------------------------------------------
# MEASURED DEFECT 2026-08-19
#
# `.form-select` draws its dropdown caret as an inline-SVG background-image and
# suppresses the native arrow with `appearance: none`, reserving 2.25rem of
# right padding for it. Two later rules re-declared the `background` SHORTHAND
# on `.form-input, .form-select, .form-textarea`. The shorthand resets every
# background-* longhand it does not mention -- including background-image --
# so the caret was erased on EVERY <select> in the portal.
#
# Measured in Chromium on /admin/audit-log, before the fix:
#     backgroundImage : "none"     <- the caret, gone
#     appearance      : "none"     <- native arrow suppressed
#     paddingRight    : "36px"     <- space held for an arrow that never paints
#
# The failure mode is why it survived so long: nothing errors, no test fails,
# and the reserved space makes the control look deliberately styled. It simply
# reads as a text box, so the only way to learn it is a dropdown is to click it.
#
# These tests are deliberately about the MECHANISM (shorthand after the caret
# rule), not about one call site, so the same mistake in a future layer is
# caught rather than only the two occurrences that were fixed.

CARET_ANCHOR = ".form-select {"


def _rules_targeting_form_select(text: str) -> list[tuple[int, str, str]]:
    """(offset, selector, body) for every rule whose selector lists .form-select.

    Comments are stripped first: the explanatory notes around these rules talk
    about `background:` in prose, and matching prose would make this test fail
    on its own documentation.
    """
    out = []
    stripped = _strip_comments(text)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", stripped):
        selector = m.group(1).strip()
        if ".form-select" not in selector:
            continue
        out.append((m.start(), selector, m.group(2)))
    return out


def test_the_select_caret_is_still_declared():
    """The control that tells a user 'this is a dropdown'."""
    css = _strip_comments(_css())
    m = re.search(
        r"\.form-select\s*\{[^}]*background-image:\s*url\(\s*[\"']?data:image/svg\+xml",
        css,
    )
    assert m, (
        "no .form-select rule declares an inline-SVG background-image caret. "
        "With `appearance: none` also set, a <select> then has NO affordance at "
        "all -- it is pixel-identical to a text input."
    )
    assert "appearance: none" in css, (
        "the caret is drawn but `appearance: none` is gone, so the browser's "
        "native arrow and the custom SVG would both paint"
    )


def test_no_rule_after_the_caret_resets_it_with_the_background_shorthand():
    """The actual defect, expressed as its mechanism.

    A `background:` shorthand in a rule that targets .form-select AND sits
    after the caret declaration silently deletes the caret. background-color
    is the correct longhand and leaves the image alone.
    """
    css = _strip_comments(_css())
    caret_at = css.find("background-image: url(\"data:image/svg+xml")
    if caret_at < 0:
        caret_at = css.find("background-image:url(\"data:image/svg+xml")
    assert caret_at >= 0, "the caret declaration was not found at all"

    offenders = []
    for offset, selector, body in _rules_targeting_form_select(css):
        if offset <= caret_at:
            continue  # earlier rules are overridden BY the caret, which is fine
        # `background:` but not `background-color:` / `background-image:` etc.
        for decl in re.finditer(r"(?<![-\w])background\s*:\s*([^;}]+)", body):
            value = decl.group(1).strip()
            offenders.append(
                f"{selector!r} sets `background: {value}` after the caret "
                "declaration; the shorthand resets background-image and erases "
                "the dropdown arrow. Use background-color."
            )
    assert not offenders, (
        "the <select> caret is being erased by a later background shorthand:\n  "
        + "\n  ".join(offenders)
    )


def test_the_select_still_reserves_room_for_its_caret():
    """Padding and caret must survive together.

    If a future edit drops the padding but keeps the image, the arrow overlaps
    the longest option's text; if it drops the image but keeps the padding, the
    control has dead space and no affordance -- which is exactly the state that
    made the measured defect look intentional.
    """
    css = _strip_comments(_css())
    m = re.search(r"\.form-select\s*\{([^}]*)\}", css)
    assert m, ".form-select has no dedicated rule any more"
    body = m.group(1)
    assert "padding-right" in body, (
        "the .form-select rule no longer reserves right padding, so the caret "
        "will sit on top of the selected option's text"
    )


def test_the_cache_bust_moved_when_the_caret_fix_shipped():
    """A stale cached app.css keeps every dropdown looking like a text box.

    app.css is served as a plain static file with no content hash, so the only
    thing that invalidates a returning browser's copy is the `?v=` query.
    """
    with open(SPA_HTML, encoding="utf-8") as fh:
        html = fh.read()
    m = re.search(r"/static/css/app\.css\?v=([\d.]+)", html)
    assert m, "app.css is no longer requested with a ?v= cache-busting query"
    assert float(m.group(1)) >= 24.1, (
        f"app.css cache-bust is v{m.group(1)}, but the select-caret fix shipped "
        "at v24.1. Returning browsers would keep serving the stylesheet in "
        "which every dropdown has no arrow."
    )
