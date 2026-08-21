"""Guards for the CSS containing-block trap that clipped every hand-rolled modal.

WHY THIS FILE EXISTS (reported by the operator with screenshots, 2026-08-13)
---------------------------------------------------------------------------
The "Customize Role" dialog rendered cut off mid-form -- the Role Name and
Description inputs were visible, everything below them was gone, and the
"Save Changes" button could not be reached at all. The page behind it appeared
to end at the same hard horizontal line. "Provision New Portal" had the same
defect. Both were unusable.

ROOT CAUSE (measured, not guessed)
    `position: fixed` does NOT always resolve against the viewport. It resolves
    against the nearest ancestor that establishes a containing block -- which
    any non-`none` `transform`, `filter`, `backdrop-filter`, `perspective`,
    `contain` or `will-change` creates.

    The routed page content is wrapped in `.page-enter`, whose route-transition
    animation was declared:

        animation: page-enter 140ms ... both;   /* <- `both` */
        will-change: opacity, transform;        /* <- permanent */

    `both` keeps the animation's transform asserted forever, and a static
    `will-change` is never released, so that wrapper was PERMANENTLY a
    containing block. Every `fixed inset-0` overlay that was not portalled out
    of the page tree was therefore sized and clipped to the content wrapper
    instead of the viewport. Measured at 1024x640:

        before: overlay 736 x 180 at (264, 151)   <- the wrapper
        after:  overlay 1024 x 620 at (0, 20)     <- the viewport

WHY THE 656-TEST SUITE MISSED IT
    Nothing in it ever opened a modal. Backend tests cannot see CSS, and the
    frontend tests asserted on source text, not on rendered geometry. A defect
    that only exists once a browser has resolved the cascade is invisible to
    both. That gap is the real finding here.

THE 2026-08-13 FIX WAS INCOMPLETE (found 2026-08-17)
    Changing `both` -> `forwards` removed the visible symptom but NOT the
    containing block. `forwards` still retains the final keyframe, and:

        `to { transform: none }` retained  -> computed matrix(1, 0, 0, 1, 0, 0)
        `to { translate: 0 0 }`  retained  -> computed `0px`

    Per spec a containing block is formed by any value other than the KEYWORD
    `none`, so an identity matrix and a `0px` translate are both fatal. The app
    merely LOOKED fixed for four days because every dialog is portalled to
    <body>, which sidesteps the wrapper entirely. A `fixed inset-0` probe
    injected into the settled wrapper measured 736x596 at (264,131) against a
    1024x640 viewport -- the same 736/x=264 recorded during the original outage.

    The real fix was to declare NO fill mode: the base style already equals the
    end state, so nothing snaps back, and both `transform` and `translate`
    revert to the keyword `none`. Re-measured after the change: the probe fills
    1024x640 at (0,0).

WHAT IS PINNED
    1. `.page-enter` must not declare ANY animation fill mode (not `both`, and
       not `forwards` either -- banning only `both` is how the bug survived).
    2. `.page-enter` must not declare `will-change`/`perspective`/`contain`.
    3. A transform-ish property in the keyframes is never paired with a fill
       mode (the coupling is the actual invariant).
    4. Every hand-rolled `fixed inset-0` overlay must be portalled to <body>.
These are cheap static checks; the geometry itself is verified in a real browser
by scripts/verify_dialog_geometry.py and scripts/verify_glass_ui.py.
"""

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "app", "frontend", "src")
CSS = os.path.join(SRC, "index.css")

# Properties that make an element a containing block for fixed descendants.
CONTAINING_BLOCK_PROPS = ("will-change", "perspective", "contain")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _page_enter_rule() -> str:
    """The body of the `.page-enter` rule (not the @keyframes of the same name)."""
    css = _read(CSS)
    m = re.search(r"\n\.page-enter\s*\{(.*?)\}", css, re.S)
    assert m, (
        "the `.page-enter` rule is gone from index.css. If the route transition "
        "was renamed, update this guard -- do not delete it, or the containing-"
        "block trap can silently come back."
    )
    return m.group(1)


def _page_enter_keyframes() -> str:
    """The body of the `@keyframes page-enter` block."""
    css = _read(CSS)
    m = re.search(r"@keyframes\s+page-enter\s*\{", css)
    assert m, (
        "the `@keyframes page-enter` block is gone from index.css. If the route "
        "transition was renamed, update this guard -- do not delete it."
    )
    # Brace-match: the block contains nested keyframe selectors, so a lazy
    # `.*?\}` would stop at the first inner brace and read almost nothing.
    open_brace = css.index("{", m.start())
    depth = 0
    for i in range(open_brace, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[open_brace + 1 : i]
    raise AssertionError("unbalanced braces in @keyframes page-enter")


# Properties that establish a containing block when RETAINED by a fill mode.
# `transform` is the famous one, but the independent transform properties count
# too: the spec keys off "any value other than the keyword `none`", so a
# retained `translate: 0px` is just as fatal as an identity matrix. That is not
# theory -- it is what the second failed fix attempt measured (see below).
RETAINED_TRANSFORM_PROPS = ("transform", "translate", "rotate", "scale")


def test_page_enter_declares_no_animation_fill_mode_at_all():
    """The wrapper must not RETAIN an animated transform-ish property.

    MEASURED HISTORY -- three states of this one declaration:

      `both`      -> transform: matrix(...)  -> containing block (the outage)
      `forwards`  -> transform: none, but resolved to the IDENTITY MATRIX,
                     and after the keyframes moved to `translate`, a retained
                     `translate: 0px`. Still a containing block. The app only
                     LOOKED fixed because every dialog is portalled to <body>.
      no fill     -> transform: none AND translate: none. Probe fills the
                     viewport: 1024x640 at (0,0). Correct.

    Any fill mode keeps the final keyframe asserted, so banning only `both`
    (which is all this file used to do) is not enough -- that is precisely how
    the defect survived from 2026-08-13 to 2026-08-17. Dropping the fill mode
    is safe because the base style already equals the end state.
    """
    body = _page_enter_rule()
    anim = re.search(r"animation:\s*([^;]+);", body)
    assert anim, "`.page-enter` no longer declares an `animation` shorthand"
    shorthand = anim.group(1)
    for mode in ("both", "forwards", "backwards"):
        assert not re.search(rf"\b{mode}\b", shorthand), (
            f"`.page-enter` uses animation-fill-mode `{mode}`. Every fill mode "
            f"keeps the final keyframe's transform-ish property asserted after "
            f"the animation ends, which makes the routed content wrapper a "
            f"containing block -- so `fixed inset-0` resolves to the ~736px "
            f"content wrapper instead of the viewport, the exact defect that "
            f"made the Customize Role dialog unusable. Declare no fill mode: "
            f"the base style is already the end state, so nothing snaps back."
        )
    # Also forbid the longhand, which would sneak past the shorthand check.
    assert not re.search(r"animation-fill-mode\s*:", body), (
        "`.page-enter` sets `animation-fill-mode` as a longhand. Same trap as "
        "the shorthand: any retained value re-creates the containing block."
    )


def test_a_transform_in_the_keyframes_is_never_paired_with_a_fill_mode():
    """The real invariant is a COUPLING, so it is tested as one.

    The 4px rise is worth keeping -- it is part of the page-composition polish.
    What is never acceptable is animating a transform-ish property AND retaining
    it. Either half alone is harmless:

        translate in keyframes + no fill mode   -> reverts to `none`. Safe.
        fill mode + opacity-only keyframes      -> nothing transform-ish. Safe.
        translate in keyframes + ANY fill mode  -> retained. CONTAINING BLOCK.

    Asserting the coupling (rather than banning transforms outright) is what
    lets this guard fail loudly on the dangerous combination without forcing
    the animation to be flattened. Both halves are re-checked here so the test
    cannot be satisfied by a stale reading of just one file region.
    """
    frames = _page_enter_keyframes()
    rule = _page_enter_rule()

    animated = [
        prop for prop in RETAINED_TRANSFORM_PROPS
        if re.search(rf"(^|[\s;{{]){prop}\s*:", frames)
    ]
    has_fill = bool(
        re.search(r"animation-fill-mode\s*:", rule)
        or re.search(r"animation:\s*[^;]*\b(both|forwards|backwards)\b", rule)
    )

    assert not (animated and has_fill), (
        f"`@keyframes page-enter` animates {animated} while `.page-enter` "
        f"declares a fill mode. The fill mode retains that value after the "
        f"animation ends, and ANY value other than the keyword `none` makes the "
        f"wrapper a containing block for `position: fixed` descendants -- "
        f"`fixed inset-0` then means the ~736px content wrapper, not the "
        f"viewport. This exact pairing shipped twice: `transform: none` under "
        f"`forwards` resolved to matrix(1,0,0,1,0,0), and `translate: 0 0` "
        f"under `forwards` resolved to `0px`. Both were measured as CLIPPED. "
        f"Drop the fill mode, or animate opacity only."
    )


def test_page_enter_does_not_declare_will_change_or_containment():
    """A static `will-change` is never released, so it creates a containing
    block for the lifetime of the element."""
    body = _page_enter_rule()
    for prop in CONTAINING_BLOCK_PROPS:
        assert not re.search(rf"(^|[\s;]){prop}\s*:", body), (
            f"`.page-enter` declares `{prop}`, which establishes a containing "
            f"block for `position: fixed` descendants and re-breaks every "
            f"overlay that is not portalled to <body>. A 140ms opacity/"
            f"transform animation does not need it."
        )


# ---------------------------------------------------------------------------
# Every hand-rolled overlay must escape the page tree.
# ---------------------------------------------------------------------------
# Belt and braces: fixing the CSS removes today's containing block, but any
# future `transform`/`filter`/`animation` on an ancestor would reintroduce the
# bug. Portalling to <body> makes an overlay structurally immune, which is why
# components/ui.tsx <Modal> has always done it.

def _tsx_files():
    out = []
    for sub in ("pages", "components"):
        base = os.path.join(SRC, sub)
        for root, _dirs, files in os.walk(base):
            for fn in files:
                if fn.endswith(".tsx"):
                    out.append(os.path.join(root, fn))
    return sorted(out)


def test_every_fixed_inset_overlay_is_portalled_to_body():
    """A `fixed inset-0` overlay left inside the routed tree is one ancestor
    transform away from being clipped."""
    offenders = []
    for path in _tsx_files():
        src = _read(path)
        if "fixed inset-0" not in src:
            continue
        if "createPortal" not in src:
            offenders.append(f"{os.path.relpath(path, REPO_ROOT)} (no createPortal at all)")
            continue
        # Count overlays vs portals: an file with more overlays than portal
        # calls has at least one inline overlay left behind.
        overlays = len(re.findall(r"fixed inset-0", src))
        portals = len(re.findall(r"createPortal\(", src))
        if overlays > portals:
            offenders.append(
                f"{os.path.relpath(path, REPO_ROOT)} "
                f"({overlays} 'fixed inset-0' overlays but only {portals} createPortal call(s))"
            )
    assert not offenders, (
        "These files render a viewport overlay that is NOT portalled to "
        "<body>:\n  " + "\n  ".join(offenders) +
        "\n\nWrap it in createPortal(<overlay/>, document.body). Otherwise any "
        "ancestor with a transform/filter/will-change makes `fixed inset-0` "
        "resolve against that ancestor and the dialog gets clipped -- which is "
        "how the Customize Role and Provision Portal dialogs shipped broken."
    )


def test_the_overlay_scan_actually_matches_real_files():
    """Mutation guard: if the glob or the marker string stops matching, the
    check above passes vacuously. Prove it still sees the real overlays."""
    hits = [p for p in _tsx_files() if "fixed inset-0" in _read(p)]
    assert len(hits) >= 4, (
        f"only {len(hits)} files matched 'fixed inset-0' -- the scan has "
        f"probably stopped finding the real overlay markup"
    )


@pytest.mark.parametrize("marker", ["max-w-3xl", "max-w-lg"])
def test_both_repaired_dialogs_cap_their_height_against_the_viewport(marker):
    """Without a viewport-relative max-height a dialog card grows past the
    screen and its footer becomes unreachable. Checked on the SAME element as
    the card, not anywhere in the file."""
    src = _read(os.path.join(SRC, "pages", "MiscPages.tsx"))
    idx = src.find(marker)
    assert idx != -1, f"the dialog card with {marker!r} is gone from MiscPages.tsx"
    tag = src[src.rfind("<", 0, idx):src.find(">", idx)]
    assert "max-h-[calc(100vh" in tag, (
        f"the dialog card with {marker!r} has no viewport-relative max-height:\n"
        f"  {tag.strip()[:200]}\n"
        f"Without it the card can grow taller than the screen and its action "
        f"buttons cannot be reached."
    )


def test_the_provision_dialogs_action_bar_is_pinned():
    """The provision form is taller than a 640px viewport, so its submit button
    must be pinned or it sits below the fold and looks absent.

    NOTE: this inspects the class list of the element that actually CONTAINS the
    'Provision Portal' button, not merely whether the word "sticky" appears
    somewhere in the file. The file-wide substring version of this test PASSED
    while this exact fix was reverted (mutation AH), because an unrelated sticky
    header elsewhere in the same file kept matching -- a textbook vacuous
    assertion. Two occurrences of "sticky" existed; only one was the fix.
    """
    src = _read(os.path.join(SRC, "pages", "MiscPages.tsx"))
    anchor = src.find("Provision Portal</>")
    assert anchor != -1, "the 'Provision Portal' submit button is gone"
    div_start = src.rfind('<div className="', 0, anchor)
    container = src[div_start:src.find(">", div_start)]
    assert "sticky" in container and "bottom-0" in container, (
        "the container holding the 'Provision Portal' button is not pinned:\n"
        f"  {container.strip()[:200]}\n"
        "It needs `sticky bottom-0` so the primary action stays on screen on "
        "short viewports."
    )


# ---------------------------------------------------------------------------
# The shared sticky-footer utility (.spa-modal-foot)
# ---------------------------------------------------------------------------
# Found by MEASUREMENT, not by report: after repairing the two clipped dialogs
# a browser sweep of every reachable dialog at 1024x640 (a 13" laptop with
# browser chrome) found two more whose action bars sat below the fold --
# "Create Demand File" step 1 (775px of content in a 608px card) and
# "Edit Demand File". Those were NOT clipped: the card scrolled, so they were
# usable, just bad. Fixing them by pinning the bar is a 4-line CSS utility that
# mirrors the already-sticky `.spa-modal-head`.
#
# Reporting those two separately from the clipped pair was the point. Treating
# "below the fold but scrollable" as the same outage as "unreachable" would
# have meant claiming two defects that did not exist.

def _css_rule(selector: str) -> str | None:
    css = _read(CSS)
    m = re.search(rf"\n{re.escape(selector)}\s*\{{(.*?)\}}", css, re.S)
    return m.group(1) if m else None


def test_the_sticky_modal_footer_utility_exists_and_pins_to_the_bottom():
    body = _css_rule(".spa-modal-foot")
    assert body is not None, (
        "`.spa-modal-foot` is gone from index.css. Dialog action bars in "
        "Demands.tsx / DemandDetail.tsx reference it; without the rule they "
        "silently scroll out of view again."
    )
    assert "position: sticky" in body.replace("  ", " "), (
        f"`.spa-modal-foot` does not declare position:sticky:\n{body}"
    )
    assert re.search(r"bottom:\s*0", body), (
        f"`.spa-modal-foot` does not pin to bottom:0, so it will not stay in "
        f"view inside the scrolling card:\n{body}"
    )
    assert re.search(r"background", body), (
        "`.spa-modal-foot` has no background, so the form content will scroll "
        "visibly underneath the pinned buttons."
    )


def test_the_sticky_footer_is_paired_with_the_sticky_header():
    """`.spa-modal-card` is the scroll container, so a sticky child pins against
    the card. If the card ever stops scrolling, both sticky bars become no-ops
    and this whole approach needs revisiting."""
    card = _css_rule(".spa-modal-card")
    assert card is not None, "`.spa-modal-card` rule is gone"
    assert re.search(r"overflow-y:\s*auto", card), (
        "`.spa-modal-card` is no longer the scroll container "
        "(overflow-y:auto). `.spa-modal-head`/`.spa-modal-foot` pin against "
        "their nearest scrolling ancestor, so they would silently stop working."
    )
    assert re.search(r"max-height:\s*calc\(100vh", card), (
        "`.spa-modal-card` lost its viewport-relative max-height, so dialogs "
        "can grow past the screen no matter what the footer does."
    )


@pytest.mark.parametrize(
    ("relpath", "button_text"),
    [
        # NOTE the anchors are UNIQUE strings, deliberately. The first draft of
        # this test anchored on "Next</button>" and matched the LIST PAGINATION
        # control at Demands.tsx:454 -- 236 lines above the wizard footer -- so
        # it failed while the fix was correctly in place. An anchor that appears
        # twice tests whichever one happens to come first, which is no test at
        # all. `data-testid="demand-submit"` occurs exactly once and sits in the
        # same footer div as the Next button.
        (os.path.join("pages", "Demands.tsx"), 'data-testid="demand-submit"'),
        (os.path.join("pages", "DemandDetail.tsx"), "Save</button>"),
    ],
)
def test_the_tall_dialogs_measured_below_the_fold_now_pin_their_actions(relpath, button_text):
    """Same targeting discipline as the provision test: walk back from the real
    button to the <div> that contains it, instead of grepping the whole file."""
    src = _read(os.path.join(SRC, relpath))
    assert src.count(button_text) == 1, (
        f"anchor {button_text!r} occurs {src.count(button_text)} times in "
        f"{relpath}; this test needs a unique anchor or it may inspect the "
        f"wrong element (see the note above)."
    )
    anchor = src.find(button_text)
    assert anchor != -1, f"{button_text!r} is gone from {relpath}"
    div_start = src.rfind('<div className="', 0, anchor)
    assert div_start != -1, f"no enclosing <div className=...> found before {button_text!r}"
    container = src[div_start:src.find(">", div_start)]
    assert "spa-modal-foot" in container, (
        f"the action bar containing {button_text!r} in {relpath} is not pinned:\n"
        f"  {container.strip()[:220]}\n"
        f"Measured at 1024x640 this bar sat below the fold. Add "
        f"`spa-modal-foot` (see index.css)."
    )


def test_the_footer_bleed_compensates_for_the_body_padding():
    """A sticky bar inside a `p-5` body floats above a transparent strip of
    scrolling content unless the padding is bled through with negative margins.
    DemandDetail's bar is inside such a body; Demands' bar is not."""
    src = _read(os.path.join(SRC, "pages", "DemandDetail.tsx"))
    anchor = src.find("Save</button>")
    div_start = src.rfind('<div className="', 0, anchor)
    container = src[div_start:src.find(">", div_start)]
    assert "-mx-5" in container, (
        "DemandDetail's pinned action bar sits inside a `p-5` modal body but "
        "does not bleed that padding (`-mx-5 ... px-5`), so the sticky bar "
        "will not reach the card edges and form rows will show in the gap:\n"
        f"  {container.strip()[:220]}"
    )


# ---------------------------------------------------------------------------
# Every overlay must identify itself as a dialog.
# ---------------------------------------------------------------------------
# Found while promoting the measurement script out of /tmp: it locates dialogs
# by `[role=dialog]`, and the two overlays that had actually been BROKEN were
# invisible to it -- because neither declared the role. Four of the six files
# already did. So the two dialogs the operator could not use were also the two
# that assistive technology announced as anonymous divs, with no modal
# semantics and no accessible name.
#
# This is a genuine accessibility defect in its own right, and it is also what
# would have made a rendered-geometry check silently skip the very dialogs it
# was written to protect. Both concerns are fixed by the same three attributes.

OVERLAY_FILES = [
    os.path.join("pages", "MiscPages.tsx"),
    os.path.join("pages", "Candidates.tsx"),
    os.path.join("pages", "ClientDetail.tsx"),
    os.path.join("components", "AssignWizard.tsx"),
    os.path.join("components", "ui.tsx"),
    os.path.join("layout", "Topbar.tsx"),
]


@pytest.mark.parametrize("relpath", OVERLAY_FILES)
def test_every_overlay_file_declares_dialog_semantics(relpath):
    """An overlay without role="dialog" + aria-modal is announced as a plain
    div: no modal boundary, and focus/virtual-cursor escape into the page
    behind it."""
    path = os.path.join(SRC, relpath)
    src = _read(path)
    n_overlays = len(re.findall(r"fixed inset-0", src))
    if n_overlays == 0:
        pytest.skip(f"{relpath} no longer renders a fixed overlay")
    assert 'role="dialog"' in src, (
        f'{relpath} renders {n_overlays} fixed overlay(s) but never declares '
        f'role="dialog". Screen readers announce it as an anonymous div, and '
        f'scripts/verify_dialog_geometry.py -- which finds dialogs by '
        f'[role=dialog] -- cannot see it, so it silently goes unmeasured.'
    )
    assert "aria-modal" in src, (
        f"{relpath} declares role=dialog but not aria-modal, so assistive tech "
        f"does not treat the content behind it as inert."
    )


@pytest.mark.parametrize("relpath", OVERLAY_FILES)
def test_every_dialog_has_an_accessible_name(relpath):
    """role=dialog with no name announces only "dialog". Either aria-label or
    aria-labelledby (preferred -- it cannot drift from the visible title)."""
    src = _read(os.path.join(SRC, relpath))
    if "fixed inset-0" not in src:
        pytest.skip(f"{relpath} no longer renders a fixed overlay")
    n_roles = len(re.findall(r'role="dialog"', src))
    n_names = len(re.findall(r"aria-label=|aria-labelledby=", src))
    assert n_names >= n_roles, (
        f"{relpath} has {n_roles} role=dialog element(s) but only {n_names} "
        f"accessible name(s) (aria-label / aria-labelledby). An unnamed dialog "
        f"is announced as just \"dialog\"."
    )


def test_the_aria_scan_is_not_vacuous():
    """If the file list rots or the marker changes, the two checks above pass
    trivially. Prove they are still inspecting real overlay markup."""
    with_overlays = [p for p in OVERLAY_FILES
                     if "fixed inset-0" in _read(os.path.join(SRC, p))]
    assert len(with_overlays) >= 5, (
        f"only {len(with_overlays)} of the {len(OVERLAY_FILES)} listed files "
        f"still contain 'fixed inset-0': {with_overlays}. The ARIA guards are "
        f"skipping almost everything -- update OVERLAY_FILES."
    )
