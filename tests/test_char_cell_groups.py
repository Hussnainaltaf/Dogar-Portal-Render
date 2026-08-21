"""Unit guards for the char-cell grouping helper in the Overlay Engine.

WHY THIS FILE EXISTS
--------------------
``_draw_char_cells`` lays one character per cell across an evenly pitched run.
Pre-printed government forms are frequently not evenly pitched: a Pakistani
CNIC grid is printed 5-7-1, with a visibly wider gap where each dash of
``34102-3634966-1`` goes.

Rendering 13 digits as one even run into that grid does not merely look
untidy -- the error accumulates. On the Allied Bank form the fifth digit landed
16.1pt right of its printed box (a whole cell wide) and the thirteenth was
pushed clean off the page, so every form the portal produced carried a twelve
digit national ID.

``_cell_slots`` is the fix: it maps character index -> grid slot, inserting one
blank slot between groups. It is deliberately a separate pure function so this
behaviour can be tested without a canvas, a template or a database.

The tests below pin two things that matter equally:

  * the grouping maths is right, and
  * the helper is INERT unless a template opts in. Every other template in the
    portal relies on the previous even-run behaviour, so a change here that
    silently shifted them would be a far worse defect than the one being fixed.
"""

import pytest

from app.services.pdf_engine import _cell_slots

# ---------------------------------------------------------------------------
# Backward compatibility: no grouping means exactly what it always did
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("group_sizes", [None, [], 0, False])
def test_absent_grouping_gives_a_plain_even_run(group_sizes):
    """Every existing template passes no group_sizes and must not move."""
    assert _cell_slots(13, group_sizes) == list(range(13))


@pytest.mark.parametrize("count", [1, 5, 8, 13, 16, 20])
def test_plain_run_is_contiguous_for_any_length(count):
    assert _cell_slots(count, None) == list(range(count))


# ---------------------------------------------------------------------------
# The grouping maths
# ---------------------------------------------------------------------------

def test_cnic_grouping_inserts_one_blank_slot_per_separator():
    """5-7-1 => digits occupy slots 0-4, 6-12 and 14."""
    assert _cell_slots(13, [5, 7, 1]) == [
        0, 1, 2, 3, 4,          # first group
        6, 7, 8, 9, 10, 11, 12,  # second group (slot 5 is the dash gap)
        14,                      # third group (slot 13 is the dash gap)
    ]


def test_grouping_consumes_exactly_one_slot_per_gap():
    """Total slots = characters + (number of groups - 1)."""
    slots = _cell_slots(13, [5, 7, 1])
    assert max(slots) + 1 == 13 + 2


@pytest.mark.parametrize(
    "count,groups,expected_span",
    [
        (13, [5, 7, 1], 15),   # CNIC
        (8, [2, 2, 4], 10),    # a dd-mm-yyyy style grid
        (11, [4, 7], 12),      # a single separator
        (6, [6], 6),           # one group is the same as no grouping
    ],
)
def test_span_grows_by_one_slot_per_separator(count, groups, expected_span):
    assert max(_cell_slots(count, groups)) + 1 == expected_span


def test_a_single_group_is_identical_to_no_grouping():
    assert _cell_slots(9, [9]) == _cell_slots(9, None)


def test_slots_are_strictly_increasing():
    """Characters must never be drawn out of order or on top of each other."""
    slots = _cell_slots(13, [5, 7, 1])
    # strict=False is deliberate: pairing a list with its own tail is a
    # ragged zip by construction (n items against n-1).
    assert all(b > a for a, b in zip(slots, slots[1:], strict=False))


# ---------------------------------------------------------------------------
# Malformed input degrades, it does not explode
# ---------------------------------------------------------------------------
# Template meta is operator-editable JSON reachable from the designer UI. A bad
# value must not stop a document being generated -- a document with a slightly
# wrong grid is recoverable, a 500 on the print button is not.

@pytest.mark.parametrize(
    "groups",
    [
        [5, 7],        # sums to 12, not 13
        [5, 7, 2],     # sums to 14
        [13, 1],       # sums to 14
        [1],           # sums to 1
    ],
)
def test_groups_that_do_not_sum_to_the_cell_count_are_ignored(groups):
    assert _cell_slots(13, groups) == list(range(13))


def test_ignoring_a_bad_group_never_drops_a_character():
    """The fallback must still place all 13 characters."""
    assert len(_cell_slots(13, [5, 7])) == 13


# ---------------------------------------------------------------------------
# The measured geometry this was built for
# ---------------------------------------------------------------------------
# The Allied Bank form's printed CNIC grid, measured from the background scan
# as the engine places it (stretched to the full 612x792 page):
#
#   verticals 389.04 .. 609.84, 15 slots, pitch 220.8/15 = 14.72pt
#   printed cell centres below were read off the same profile
#
# This test is the one that would have caught the original defect: it computes
# where each digit is actually drawn and compares it against the paper.

PITCH = 14.72
ORIGIN = 389.04
PRINTED_CENTRES = [
    396.12, 410.46, 424.86, 439.26, 453.66,
    484.92, 499.26, 513.66, 528.12, 542.52, 556.92, 571.26,
    602.64,
]
PAGE_WIDTH = 612.0
# One digit is ~6.1pt wide at 11pt Helvetica-Bold; half of that either side.
DIGIT_HALF_WIDTH = 3.1


def _drawn_centres(groups):
    return [
        ORIGIN + slot * PITCH + PITCH / 2
        for slot in _cell_slots(13, groups)
    ]


def test_grouped_layout_lands_every_digit_in_its_printed_box():
    """Max deviation must stay well inside a 14.7pt cell."""
    errors = [
        abs(drawn - printed)
        for drawn, printed in zip(
            _drawn_centres([5, 7, 1]), PRINTED_CENTRES, strict=True
        )
    ]
    assert max(errors) < PITCH / 2, (
        f"worst digit is {max(errors):.2f}pt off centre, which is more than "
        f"half a cell -- it would render in the neighbouring box"
    )
    # Measured on the real render: 1.78pt. Kept tight so a regression that
    # merely stays "inside the box" still trips.
    assert max(errors) < 2.5


def test_grouped_layout_keeps_every_digit_on_the_page():
    """The original defect: the 13th digit was drawn past the page edge."""
    right_edge = max(_drawn_centres([5, 7, 1])) + DIGIT_HALF_WIDTH
    assert right_edge <= PAGE_WIDTH, (
        f"last digit reaches {right_edge:.2f}pt on a {PAGE_WIDTH}pt page"
    )


def test_an_even_run_reproduces_the_original_defect():
    """Proof the grouping is load-bearing, not decoration.

    Without grouping -- at the *old* 18pt pitch and origin -- digits drift more
    than a full cell and the last one leaves the page. If this test ever starts
    failing, the claim in the migration's docstring is wrong.
    """
    old_origin, old_pitch = 388.8, 18.0
    drawn = [old_origin + i * old_pitch + old_pitch / 2 for i in range(13)]
    errors = [
        abs(d - p) for d, p in zip(drawn, PRINTED_CENTRES, strict=True)
    ]
    assert max(errors) > old_pitch / 2, "the old layout should be >half a cell out"
    assert max(drawn) + DIGIT_HALF_WIDTH > PAGE_WIDTH, (
        "the old layout should run off the page"
    )
