"""Guards for the generated FontAwesome subset stylesheet.

BACKGROUND
----------
Both shells (the SPA's index.html and the Jinja base.html + the four standalone
auth pages) used to render-block on the complete FontAwesome 6.4.0 stylesheet:
101,965 bytes, 22,373 gzipped, of which 69% is the per-icon `content:` rules for
all 1,856 icons in the free set. This portal references 183 of them.

`scripts/build_fa_subset.py` now emits `fa-subset.min.css`, keeping FontAwesome's
machinery verbatim and dropping only the unused icon rules — 23,779 bytes, 4,448
gzipped.

WHY THIS NEEDS GUARDING MORE THAN MOST THINGS
---------------------------------------------
The failure mode is *adding* an icon. Write `fa-truck-fast` in a new component,
forget to regenerate, and it renders as a blank box — no error, no failing
functional test, and it will only be noticed if someone happens to look at that
screen. The three ways it can break are each pinned below:

  1. the subset is stale with respect to the generator (someone edited markup
     and did not regenerate);
  2. an icon referenced in source has no rule in the subset;
  3. a NEW runtime-composed name (`fa-align-${a}`) appeared, which a static
     scan cannot see, without being declared in the generator's DYNAMIC map.

(3) is the subtle one and is the reason three such sites were missed on the
first pass of this work.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "build_fa_subset.py"
FULL = ROOT / "app" / "static" / "vendor" / "css" / "all.min.css"
SUBSET = ROOT / "app" / "static" / "vendor" / "css" / "fa-subset.min.css"


def _load_generator():
    spec = importlib.util.spec_from_file_location("build_fa_subset", GENERATOR)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gen():
    assert GENERATOR.is_file(), f"missing generator: {GENERATOR}"
    return _load_generator()


@pytest.fixture(scope="module")
def subset_css() -> str:
    assert SUBSET.is_file(), (
        "fa-subset.min.css is missing. Run: python scripts/build_fa_subset.py"
    )
    return SUBSET.read_text(encoding="utf-8")


def _icon_names(css: str) -> set[str]:
    """Icon names that have a `content:` rule in the given stylesheet."""
    return set(re.findall(r"\.(fa-[a-z0-9-]+):{1,2}before\s*[,{]", css))


# ---------------------------------------------------------------------------
# 1. The committed file must match a fresh generation.
# ---------------------------------------------------------------------------


def test_subset_is_not_stale() -> None:
    """Someone changed the markup and forgot to regenerate."""
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, (
        "fa-subset.min.css does not match what the generator would produce now.\n"
        "Run: python scripts/build_fa_subset.py\n"
        f"{result.stdout}{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 2. Nothing the app actually uses may be missing.
# ---------------------------------------------------------------------------


def test_every_referenced_icon_survives(gen, subset_css: str) -> None:
    """The whole point: no icon in the source may render as a blank box."""
    full_icons = _icon_names(FULL.read_text(encoding="utf-8"))
    subset_icons = _icon_names(subset_css)
    used = gen.collect_used()

    # Only names that are genuinely FontAwesome icons are relevant; the rest of
    # the tokens are machinery classes (fa-solid, fa-fw, fa-spin, ...).
    referenced = {t for t in used if t in full_icons}
    missing = sorted(referenced - subset_icons)
    assert not missing, (
        f"{len(missing)} icon(s) referenced in source but absent from the "
        f"subset — these will render as blank boxes: {missing}"
    )
    assert len(referenced) > 100, (
        f"only {len(referenced)} icons detected; the scanner is probably not "
        "reaching the source tree any more"
    )


def test_dynamic_icon_names_are_all_declared(gen) -> None:
    """A NEW runtime-composed icon name must be added to DYNAMIC.

    A static scan sees `fa-align-${a}` as the token `fa-align`, which is not a
    real icon, so the composed names would be pruned silently. This test finds
    every interpolation site and requires that the generator declares at least
    one concrete name sharing that prefix.
    """
    pattern = re.compile(r"fa-(?:[a-z0-9]+-)*\$\{")
    declared = set(gen.DYNAMIC)
    offenders: list[str] = []

    for rel, exts in gen.SCAN:
        base = ROOT / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in exts:
                continue
            if gen.SKIP_DIRS & set(path.relative_to(ROOT).parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(text.splitlines(), 1):
                for m in pattern.finditer(line):
                    stem = m.group(0)[:-2]  # drop the trailing "${"
                    # "fa-align-" -> any declared name starting with it;
                    # "fa-"       -> any declared name at all.
                    if not any(d.startswith(stem) for d in declared):
                        offenders.append(
                            f"{path.relative_to(ROOT)}:{lineno}: {stem}${{...}}"
                        )

    assert not offenders, (
        "runtime-composed FontAwesome icon name(s) with no matching entry in "
        "scripts/build_fa_subset.py::DYNAMIC. A static scan cannot see these, "
        "so they would be pruned from the subset and render as blank boxes. "
        "Add the concrete names to DYNAMIC and regenerate:\n  "
        + "\n  ".join(offenders)
    )


def test_declared_dynamic_names_are_real_icons(gen) -> None:
    """Typos in DYNAMIC would be silently useless — catch them."""
    full_icons = _icon_names(FULL.read_text(encoding="utf-8"))
    bogus = sorted(n for n in gen.DYNAMIC if n not in full_icons)
    assert not bogus, f"DYNAMIC lists names that are not FontAwesome icons: {bogus}"


# ---------------------------------------------------------------------------
# 3. The machinery must survive, or every icon breaks at once.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "needle",
    [
        "@font-face",  # the faces themselves
        "Font Awesome 6 Free",  # family binding for fa-solid / fa-regular
        "Font Awesome 6 Brands",  # family binding for fa-brands
        "fa-spin",  # animation utility used by loading states
        "@keyframes",  # ...and its keyframes
        "fa-fw",  # fixed-width, used across the sidebar
    ],
)
def test_machinery_is_preserved(subset_css: str, needle: str) -> None:
    assert needle in subset_css, (
        f"the subset lost FontAwesome machinery ({needle!r}); the generator must "
        "only prune per-icon content rules"
    )


def test_subset_is_materially_smaller(subset_css: str) -> None:
    full = FULL.read_text(encoding="utf-8")
    assert len(subset_css) < len(full) * 0.5, (
        f"subset is {len(subset_css)} bytes against {len(full)}; the pruning is "
        "not doing its job"
    )


# ---------------------------------------------------------------------------
# 4. Every shell must reference the subset, not the full stylesheet.
# ---------------------------------------------------------------------------


# The Jinja shells (base/login/forgot_password/reset_password/contact_support)
# were removed with the rest of that stack in the Jinja2 eradication pass; they
# are no longer listed because every one of them could only ever skip, which
# reads as coverage while asserting nothing. The React SPA shell below is the
# only shell that ships, and it is checked in BOTH forms: the source template
# here and the built output in test_built_spa_shell_links_the_subset.
SHELLS = [
    "app/frontend/index.html",
]


@pytest.mark.parametrize("rel", SHELLS)
def test_shell_links_the_subset(rel: str) -> None:
    path = ROOT / rel
    if not path.is_file():
        pytest.skip(f"{rel} not present")
    text = path.read_text(encoding="utf-8")
    # Comments legitimately mention all.min.css when explaining the change.
    code = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    assert 'href="/static/vendor/css/fa-subset.min.css"' in code, (
        f"{rel} does not link the FontAwesome subset"
    )
    assert "all.min.css" not in code, (
        f"{rel} still render-blocks on the full FontAwesome stylesheet"
    )


def test_built_spa_shell_links_the_subset() -> None:
    """The served SPA shell, not just the source template."""
    built = ROOT / "app" / "static" / "spa" / "index.html"
    if not built.is_file():
        pytest.skip("SPA has not been built")
    code = re.sub(
        r"<!--.*?-->", "", built.read_text(encoding="utf-8"), flags=re.DOTALL
    )
    assert "fa-subset.min.css" in code, (
        "the built SPA shell is stale — run `npm run build` in app/frontend"
    )
    assert "all.min.css" not in code
