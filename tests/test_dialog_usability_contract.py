"""Every dialog must be reachable by mouse AND operable from the keyboard.

WHY THIS FILE EXISTS
--------------------
On 2026-08-13 the operator sent screenshots of the "Customize Role" dialog
rendered cut off mid-form -- Role Name and Description visible, everything
below them gone, the Save button unreachable. "Provision New Portal" was
broken identically. Both dialogs were completely unusable while 656 automated
tests were green, because **nothing in the suite had ever opened a dialog**.

Two separate defects came out of that report:

1.  *Geometry.* `.page-enter` declared `animation-fill-mode: both` plus a
    static `will-change`, which permanently made the routed content wrapper a
    containing block. Any `fixed inset-0` overlay that was not portalled to
    <body> was clipped to that wrapper instead of the viewport.
2.  *Keyboard.* An audit the next day found the keyboard half of "usable" had
    never been implemented consistently: three dialogs in MiscPages.tsx and one
    in ClientDetail.tsx had no Escape handling, no scroll lock and no focus
    management at all. That fails silently rather than visibly -- a dialog a
    keyboard-only operator cannot dismiss is exactly as broken as a clipped one
    is for a mouse user, and a dialog whose Tab walks out into the page behind
    it makes its own `aria-modal="true"` a lie.

`scripts/verify_dialog_geometry.py` measures the rendered result in a real
browser, which is the only way to catch (1) definitively. But it needs a
running server, a login and a browser, so it cannot gate every commit. This
module is the permanent, dependency-free counterpart: it pins the *structural
preconditions* that made both defects possible, so a regression fails in CI in
under a second rather than waiting for someone to run the browser sweep.

WHAT THIS FILE DELIBERATELY DOES NOT CLAIM
------------------------------------------
Passing here does NOT prove a dialog renders correctly. Static analysis cannot
resolve a CSS cascade. It proves the specific, known causes of the outage
cannot silently return. The browser sweep remains the authority on rendered
geometry, and section 14.3 of PROJECT_DOCUMENTATION.md says so.
"""
from __future__ import annotations

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO_ROOT, "app", "frontend", "src")
HOOK = os.path.join(SRC, "hooks", "useDialogKeys.ts")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# Every file that renders a modal overlay. Kept in step with
# tests/test_fixed_overlay_containing_block.py::OVERLAY_FILES -- a test below
# asserts the two lists agree, so adding an overlay to one and forgetting the
# other is itself a failure.
DIALOG_FILES = [
    os.path.join("pages", "MiscPages.tsx"),
    os.path.join("pages", "Candidates.tsx"),
    os.path.join("pages", "ClientDetail.tsx"),
    os.path.join("components", "AssignWizard.tsx"),
    os.path.join("components", "ui.tsx"),
    os.path.join("layout", "Topbar.tsx"),
]


# ---------------------------------------------------------------------------
# 1. The shared keyboard/focus hook must exist and keep its guarantees
# ---------------------------------------------------------------------------
def test_the_shared_dialog_keyboard_hook_exists():
    assert os.path.exists(HOOK), (
        "hooks/useDialogKeys.ts is gone. Six hand-rolled copies of this logic "
        "is how four dialogs ended up with no Escape handling in the first "
        "place; deleting the shared hook invites that back."
    )


@pytest.mark.parametrize("behaviour,pattern", [
    ("Escape closes",        r"e\.key\s*===\s*['\"]Escape['\"]"),
    ("Tab is intercepted",   r"e\.key\s*!==\s*['\"]Tab['\"]|e\.key\s*===\s*['\"]Tab['\"]"),
    ("background scroll lock", r"lockBodyScroll\(\)"),
    ("scroll lock released",   r"unlockBodyScroll\(\)"),
    ("focus moves in",         r"target\?\.focus\(\)|\.focus\(\)"),
    ("shift-tab handled",      r"shiftKey"),
])
def test_the_hook_still_implements(behaviour, pattern):
    """Each of these is a documented guarantee of the hook. Losing any one of
    them silently degrades every dialog in the app at once, which is precisely
    the blast radius that justifies a shared hook having its own tests."""
    src = _read(HOOK)
    assert re.search(pattern, src), f"useDialogKeys no longer implements: {behaviour}"


def test_focus_is_restored_to_whatever_opened_the_dialog():
    """Without this a keyboard user is dumped at the top of the document every
    time they close a dialog, and must Tab all the way back."""
    src = _read(HOOK)
    assert "opener" in src and re.search(r"opener\??\.focus\(\)", src), (
        "the hook no longer restores focus to the opening element on close"
    )


def test_the_scroll_lock_is_reference_counted_not_a_local_save_restore():
    """A local `body.style.overflow` save/restore is a trap this project has
    already fallen into: two overlapping dialogs each saved the ALREADY-LOCKED
    value as their 'previous' state, so the last to close restored `hidden` and
    left the entire portal unable to scroll."""
    lock = _read(os.path.join(SRC, "lib", "scrollLock.ts"))
    body = re.sub(r"/\*.*?\*/", "", lock, flags=re.S)
    body = re.sub(r"^\s*//.*$", "", body, flags=re.M)

    # Check the STRUCTURE of the counting, not the identifier. Mutation JH
    # renamed `count` to `COUNTLESS`, and an earlier regex that merely looked
    # for the word "count" happily matched the renamed version and reported
    # green -- the test was pinning a variable name instead of a behaviour.
    #
    # The invariant that actually matters has three parts, and all three must
    # hold or overlapping dialogs can strand the page in overflow:hidden:
    lock_fn = re.search(r"export function lockBodyScroll[^{]*\{(.*?)\n\}", body, re.S)
    unlock_fn = re.search(r"export function unlockBodyScroll[^{]*\{(.*?)\n\}", body, re.S)
    assert lock_fn and unlock_fn, "scrollLock.ts no longer exports the lock/unlock pair"

    ident = r"[A-Za-z_$][\w$]*"
    # 1. lock increments a module-level counter
    assert re.search(rf"{ident}\s*(\+= *1|\+\+)", lock_fn.group(1)), (
        "lockBodyScroll no longer increments a counter -- two overlapping "
        "dialogs will each think they own the lock"
    )
    # 2. unlock decrements it
    assert re.search(rf"{ident}\s*(-= *1|--)|{ident}\s*-\s*1", unlock_fn.group(1)), (
        "unlockBodyScroll no longer decrements the counter"
    )
    # 3. the body style is only restored when the counter reaches zero
    assert re.search(rf"if\s*\(\s*{ident}\s*===?\s*0\s*\)", unlock_fn.group(1)), (
        "unlockBodyScroll restores body overflow unconditionally. The FIRST "
        "dialog to close will unlock the page while a second is still open -- "
        "or, worse, restore an already-locked 'hidden' as the 'previous' value "
        "and leave the whole portal unscrollable."
    )
    hook = _read(HOOK)
    # Strip comments first. The hook's own doc comment *describes* the
    # `document.body.style.overflow` trap, and an earlier version of this test
    # matched that prose and failed -- a test that fires on documentation of a
    # bug rather than the bug itself is worse than useless, because the obvious
    # way to "fix" it is to delete the explanation.
    code = re.sub(r"/\*.*?\*/", "", hook, flags=re.S)
    code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
    assert "document.body.style.overflow" not in code, (
        "useDialogKeys is manipulating body overflow directly again instead of "
        "going through the reference-counted lock"
    )


# ---------------------------------------------------------------------------
# 2. Every dialog site must actually USE the hook
# ---------------------------------------------------------------------------
def _dialog_sites() -> list[tuple[str, str]]:
    """(file, snippet) for every element carrying role="dialog"."""
    sites: list[tuple[str, str]] = []
    for rel in DIALOG_FILES:
        path = os.path.join(SRC, rel)
        if not os.path.exists(path):
            continue
        src = _read(path)
        for m in re.finditer(r'role=["\']dialog["\']', src):
            sites.append((rel, src[max(0, m.start() - 500):m.start() + 200]))
    return sites


def test_there_are_dialog_sites_to_check():
    """A guard on the guard. If the role="dialog" convention were renamed, the
    parametrised tests below would silently collapse to zero cases and report
    green while checking nothing."""
    sites = _dialog_sites()
    assert len(sites) >= 6, (
        f"only {len(sites)} role=\"dialog\" elements found across "
        f"{len(DIALOG_FILES)} files -- the detection is probably broken"
    )


# The four behaviours a modal owes the keyboard, and how to recognise each one
# whether it comes from the shared hook or from an equivalent inline effect.
#
# Checking *behaviour* rather than `"useDialogKeys" in src` is deliberate.
# pages/Candidates.tsx implements the full contract inline (it predates the
# hook and has extra roving-tablist logic layered on top), so an import-name
# assertion would have flagged a correct file. Conversely layout/Topbar.tsx
# imported nothing and genuinely was broken. A test that cannot tell those two
# apart is not measuring the thing that matters.
KEYBOARD_CONTRACT = {
    "Escape closes":  (r"useDialogKeys|['\"]Escape['\"]",),
    "focus trap":     (r"useDialogKeys|shiftKey",),
    "scroll lock":    (r"useDialogKeys|lockBodyScroll\(",),
    "focus restore":  (r"useDialogKeys|prevFocus|opener",),
}


@pytest.mark.parametrize("relpath", DIALOG_FILES)
@pytest.mark.parametrize("behaviour", sorted(KEYBOARD_CONTRACT))
def test_every_dialog_file_honours_the_keyboard_contract(relpath, behaviour):
    """A dialog with no focus trap and no scroll lock, while declaring
    `aria-modal="true"`, tells assistive technology a lie: Tab walks straight
    out into the page behind it and the body keeps scrolling.

    This is not hypothetical. The 2026-08-14 audit found three MiscPages
    dialogs and one in ClientDetail with no keyboard handling whatsoever, and
    on 2026-08-16 this very test caught three more in layout/Topbar.tsx
    (Change Password, My Profile and the mobile search sheet) -- all three
    `aria-modal="true"`, none of them trapping focus or locking scroll."""
    path = os.path.join(SRC, relpath)
    if not os.path.exists(path):
        pytest.skip(f"{relpath} does not exist")
    src = _read(path)
    if 'role="dialog"' not in src and "role='dialog'" not in src:
        pytest.skip(f"{relpath} renders no dialog")
    (pattern,) = KEYBOARD_CONTRACT[behaviour]
    assert re.search(pattern, src), (
        f'{relpath} renders a role="dialog" overlay but shows no sign of '
        f'"{behaviour}". Either call useDialogKeys (preferred) or implement '
        f"the behaviour inline."
    )


@pytest.mark.parametrize("relpath", DIALOG_FILES)
def test_every_dialog_declares_modal_semantics(relpath):
    """`role="dialog"` without `aria-modal` is announced as a plain grouping:
    no modal boundary, and the virtual cursor wanders into the inert page."""
    path = os.path.join(SRC, relpath)
    if not os.path.exists(path):
        pytest.skip(f"{relpath} does not exist")
    src = _read(path)
    n_dialog = len(re.findall(r'role=["\']dialog["\']', src))
    if not n_dialog:
        pytest.skip(f"{relpath} renders no dialog")
    n_modal = len(re.findall(r'aria-modal=["\']?\{?["\']?true', src))
    assert n_modal >= n_dialog, (
        f"{relpath} has {n_dialog} role=\"dialog\" element(s) but only "
        f"{n_modal} aria-modal=\"true\" -- at least one dialog claims to be a "
        f"dialog without claiming to be modal"
    )


# ---------------------------------------------------------------------------
# 3. The geometry preconditions
# ---------------------------------------------------------------------------
def test_the_modal_component_portals_to_body():
    """The original outage in one line: an overlay that is not portalled is
    clipped by any ancestor that happens to be a containing block."""
    src = _read(os.path.join(SRC, "components", "ui.tsx"))
    assert "createPortal" in src and "document.body" in src, (
        "components/ui.tsx <Modal> no longer portals to document.body; a "
        "transformed/animated ancestor can clip it to a wrapper again"
    )


@pytest.mark.parametrize("relpath", DIALOG_FILES)
def test_no_dialog_file_reintroduces_a_static_will_change(relpath):
    """`will-change` (and `animation-fill-mode: both`) on a routed wrapper is
    what made the wrapper a permanent containing block. Inline styles in the
    dialog files themselves are the easiest way to bring it back."""
    path = os.path.join(SRC, relpath)
    if not os.path.exists(path):
        pytest.skip(f"{relpath} does not exist")
    src = _read(path)
    hits = re.findall(r"will-?[Cc]hange\s*[:=]\s*['\"]?(transform|filter|opacity)", src)
    assert not hits, (
        f"{relpath} declares a static will-change ({hits}), which creates a "
        f"containing block and can clip fixed-position overlays"
    )


def test_the_dialog_file_list_matches_the_overlay_containing_block_suite():
    """Two suites protect the same set of files from two different angles. If
    they drift, a newly added dialog gets checked by one and missed by the
    other -- and the gap is invisible because both suites stay green."""
    other = os.path.join(REPO_ROOT, "tests", "test_fixed_overlay_containing_block.py")
    src = _read(other)
    block = re.search(r"OVERLAY_FILES\s*=\s*\[(.*?)\]", src, re.S)
    assert block, "OVERLAY_FILES no longer parseable in the sibling suite"
    names = set(re.findall(r'["\']([\w.]+\.tsx)["\']', block.group(1)))
    mine = {os.path.basename(p) for p in DIALOG_FILES}
    assert names == mine, (
        f"dialog file lists have drifted.\n"
        f"  only in test_fixed_overlay_containing_block: {sorted(names - mine)}\n"
        f"  only in test_dialog_usability_contract:      {sorted(mine - names)}"
    )


# ---------------------------------------------------------------------------
# 4. The browser sweep must remain runnable and honest
# ---------------------------------------------------------------------------
def test_the_browser_geometry_sweep_still_exists_and_separates_clipped_from_belowfold():
    """Conflating "unusable" with "needs a scroll" would either hide a real
    outage or cry wolf about a working dialog. The script's first version did
    exactly the latter for "Create Demand File"."""
    path = os.path.join(REPO_ROOT, "scripts", "verify_dialog_geometry.py")
    assert os.path.exists(path), "scripts/verify_dialog_geometry.py is gone"
    src = _read(path)
    assert "CLIPPED" in src and "BELOWFOLD" in src, (
        "the sweep no longer distinguishes CLIPPED (unusable) from BELOWFOLD "
        "(usable but unpinned)"
    )
    assert "scrollIntoView" in src, (
        "the sweep no longer tests whether scrolling can reach a control, "
        "which is the only thing separating the two verdicts"
    )
    cases = re.search(r"CASES:\s*list\[tuple\[str, str, str\]\]\s*=\s*\[(.*?)\n\]", src, re.S)
    assert cases, "CASES list no longer parseable"
    n = len(re.findall(r'^\s*\(', cases.group(1), re.M))
    assert n >= 11, f"the sweep now covers only {n} dialogs; it covered 11"
