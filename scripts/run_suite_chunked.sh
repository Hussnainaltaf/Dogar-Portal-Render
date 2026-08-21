#!/usr/bin/env bash
# ============================================================================
# run_suite_chunked.sh — run the backend suite in separate pytest processes.
#
# WHY THIS EXISTS (measured, 2026-08-17)
# A single `pytest tests/` process is killed by the kernel partway through this
# sandbox's 985 MB ceiling. It died at case #438 of 924, inside
# test_document_route_guards.py, leaving NO summary line -- just dots ending at
# 46%. That failure mode is dangerous because a truncated log looks like a
# clean run if you only skim it: no "F", no traceback, no "failed".
#
# The module itself is fine: run alone it is 40/40 green, while MemAvailable
# fell 182 MB -> 148 MB over 48s. So the constraint is the ENVIRONMENT, and
# the fix is to bound each process's lifetime rather than to skip tests or
# soften assertions.
#
# Each chunk is a fresh interpreter, so memory is reclaimed between chunks.
# Exit code is non-zero if ANY chunk fails, and every chunk's summary is
# printed, so a partial kill can never be mistaken for success.
#
# THE SECOND-ORDER TRAP (measured 2026-08-17, after the first fix)
# Chunking alone is not enough, and neither is "a killed chunk reports zero
# outcomes". A real run produced:
#
#     chunk 9: exit=137 passed=97 failed=0 skipped=0
#
# 137 is SIGKILL. That chunk had already reported 97 passing tests before the
# OOM killer took it, so the zero-outcome heuristic did not fire; the chunk
# merely looked like a healthy one with an odd exit code, and every module in
# it that had not yet run disappeared from the totals. TOTAL came out
# passed=837 for a suite of ~925 -- 88 tests silently unaccounted for.
#
# So truncation is now detected from pytest's OWN final summary line (absent
# whenever the process is killed, regardless of how far it got) plus any exit
# code >= 128, and a truncated chunk is RE-RUN one module at a time rather
# than having its partial counts trusted. A module that cannot finish even in
# its own process is reported as killed and fails the run.
#
# Usage: bash scripts/run_suite_chunked.sh [chunk_size]
# ============================================================================
set -uo pipefail

cd "$(dirname "$0")/.."

CHUNK="${1:-6}"           # modules per pytest process (6 measured safe here)
LOG=/tmp/suite_chunked.log
: > "$LOG"

# Fail fast, and legibly, when there is no interpreter to run.
#
# WHY (measured 2026-08-21): this script was run inside an EXTRACTED RELEASE
# ARCHIVE, which deliberately ships no venv/ and no node_modules/. Every chunk
# exited 127 ("./venv/bin/python: No such file or directory"), which the
# truncation logic correctly read as "died before reporting" -- so the run ended
# with "bad chunks=66 (of which killed/no-summary=66)" and "RESULT: FAIL".
#
# That output is indistinguishable from 66 chunks being OOM-killed, i.e. from a
# catastrophic product failure, when the actual cause was a missing dependency
# install. A harness whose worst-looking failure mode is really "you forgot step
# one" costs someone a long detour, so say it once, plainly, and stop.
if [ ! -x ./venv/bin/python ]; then
  echo "FATAL: ./venv/bin/python is missing or not executable." | tee -a "$LOG"
  echo "       Nothing was run -- this is NOT a test failure." | tee -a "$LOG"
  echo "       Dependencies are intentionally not shipped in the release" | tee -a "$LOG"
  echo "       archive. Create the environment first (see START_HERE.md):" | tee -a "$LOG"
  echo "         python3 -m venv venv && ./venv/bin/pip install -r requirements.txt" \
      | tee -a "$LOG"
  exit 2
fi

mapfile -t MODULES < <(find tests -maxdepth 1 -name 'test_*.py' | sort)
TOTAL=${#MODULES[@]}
echo "modules=$TOTAL chunk_size=$CHUNK" | tee -a "$LOG"

PASS=0; FAIL=0; SKIP=0; BAD_CHUNKS=0; KILLED=0; RETRIED=0
# Set by run_slice() on every call.
R_P=0; R_F=0; R_S=0; R_CODE=0; R_TRUNCATED=0; R_OUT=""

# ---------------------------------------------------------------------------
# run_slice <modules...> -- run one pytest process and classify its output.
#
# Sets R_P / R_F / R_S (outcome counts), R_CODE (exit status), R_TRUNCATED
# (1 when the process died before pytest could finish reporting) and R_OUT.
# ---------------------------------------------------------------------------
run_slice() {
  # No --timeout: pytest-timeout is NOT installed in this venv (verified), and
  # passing the flag would make every chunk error out on an unknown option --
  # which would have looked like a mass test failure.
  #
  # `-o addopts= -v` is deliberate, and both halves are required (measured).
  # pytest.ini sets `addopts = -ra -q`. Passing another -q makes it -qq,
  # which suppresses the "N passed" summary ENTIRELY -- output becomes bare
  # dots. Worse, a bare `-v` does NOT win against the ini -q: verified, it
  # still emitted dots and `grep -c PASSED` returned 0, i.e. every passing
  # chunk would have been misreported as killed. Clearing addopts first
  # restores both the per-test PASSED/FAILED lines and the summary.
  R_OUT=$(./venv/bin/python -m pytest "$@" -p no:randomly -o addopts= -v 2>&1)
  R_CODE=$?

  # Outcomes are counted ONLY from the verbose progress lines, which always
  # look like `tests/test_x.py::test_y PASSED  [ 42%]`.
  #
  # MEASURED BUG in the first version (2026-08-17): the patterns were bare
  # (`grep -cE "FAILED|ERROR"`), so every failure was counted TWICE -- once on
  # its verbose progress line and once again on pytest's short-summary line
  # (`FAILED tests/test_x.py::test_y - AssertionError`). `-o addopts=` clears
  # the ini's `-ra`, but it does NOT silence the short summary: pytest's
  # built-in default for -r is `fE`, so FAILED/ERROR still get a second line.
  # One real failure was therefore reported as `failed=2`. That inflates, so
  # it could never hide a failure, but a wrong count is still a wrong claim.
  #
  # MEASURED BUG in the third version (2026-08-17): the node-id was matched
  # with `[^ ]+`, i.e. "no spaces". Parametrised ids routinely CONTAIN spaces,
  # because the id is derived from the parameter value:
  #
  #   tests/test_fresh_install_geometry.py::test_every_shipped_template_is_\
  #   covered_by_the_snapshot[Allied Bank Deposit Form] PASSED
  #
  # `[^ ]+` stops at the space inside `[Allied Bank Deposit Form]`, so the
  # line failed to match and the test was never counted. On one module this
  # was 48 counted out of 59 real outcomes; across the suite it lost 71 of
  # 927. Nothing looked wrong: every chunk said exit=0 and the TOTAL was
  # simply too small, which is precisely the class of silent under-reporting
  # this script exists to prevent.
  #
  # The node-id is now matched with `.+` and the outcome word is anchored to
  # the END of the id by requiring the literal space before it, so a test
  # whose parameter value contains the word PASSED still cannot self-report.
  R_P=$(echo "$R_OUT" | grep -cE '^tests/.+::.+ PASSED' || true)
  R_F=$(echo "$R_OUT" | grep -cE '^tests/.+::.+ (FAILED|ERROR)' || true)
  R_S=$(echo "$R_OUT" | grep -cE '^tests/.+::.+ SKIPPED' || true)
  # A module that fails to import never produces a `::` line at all; pytest
  # reports it as `ERROR tests/test_x.py - ImportError`. Counted separately so
  # a broken import can never vanish into a zero-outcome chunk.
  local collect_errors
  collect_errors=$(echo "$R_OUT" | grep -cE '^ERROR tests/' || true)
  R_F=$(( R_F + collect_errors ))

  # ---- truncation detector -------------------------------------------------
  # MEASURED BUG in the second version (2026-08-17): truncation was inferred
  # from "the chunk reported zero outcomes". That is too weak. Chunk 9 of a
  # real run exited 137 (SIGKILL, the OOM killer) *after* 97 tests had already
  # reported PASSED. It therefore looked like a healthy chunk with a stray
  # non-zero exit, and the modules that never got to run simply vanished from
  # the totals -- the exact "truncated log looks clean" failure this script
  # was written to eliminate, reintroduced one level up.
  #
  # The reliable signal is pytest's own final summary line. If the process was
  # killed, that line was never printed, no matter how many tests had passed.
  # Exit codes >= 128 (signal death) are treated as truncated too, since
  # pytest itself only ever returns 0-5.
  R_TRUNCATED=0
  if ! echo "$R_OUT" | grep -qE '^=+ .*(passed|failed|error|skipped|no tests ran).* in [0-9]'; then
    R_TRUNCATED=1
  fi
  if [ "$R_CODE" -ge 128 ]; then
    R_TRUNCATED=1
  fi

  # ---- counter self-check --------------------------------------------------
  # Cross-foot my own tally against pytest's authoritative summary line, and
  # ABORT on disagreement. Every counting bug in this script's history was
  # invisible precisely because nothing compared the two numbers: the run
  # reported exit=0 for every chunk and simply produced a total that was too
  # small. A tally that cannot be checked is not evidence.
  #
  # Only counted outcome words are summed; `deselected` and `warnings` are
  # deliberately excluded because they are not test outcomes.
  if [ "$R_TRUNCATED" -eq 0 ]; then
    local summary reported
    summary=$(echo "$R_OUT" \
      | grep -E '^=+ .*(passed|failed|error|skipped|no tests ran).* in [0-9]' | tail -1)
    reported=$(echo "$summary" | grep -oE '[0-9]+ (passed|failed|error|errors|skipped|xfailed|xpassed)' \
      | awk '{s+=$1} END {print s+0}')
    if [ "$(( R_P + R_F + R_S ))" -ne "$reported" ]; then
      echo "FATAL: outcome counter disagrees with pytest." >&2
      echo "  counted passed=$R_P failed=$R_F skipped=$R_S (sum $(( R_P + R_F + R_S )))" >&2
      echo "  pytest summary: $summary  (sum $reported)" >&2
      echo "  slice: $*" >&2
      echo "Refusing to report a total that does not add up." >&2
      exit 2
    fi
  fi
}

i=0
while [ "$i" -lt "$TOTAL" ]; do
  slice=("${MODULES[@]:i:CHUNK}")
  n=$(( i / CHUNK + 1 ))

  run_slice "${slice[@]}"

  if [ "$R_TRUNCATED" -eq 1 ]; then
    # Do NOT keep the partial counts: they under-report by however many
    # modules never ran. Re-run the slice one module at a time instead, so
    # each module gets its own memory budget. This is a RECOVERY, not a
    # downgrade -- every module still executes, and any module that cannot
    # complete even alone is reported as killed and fails the run.
    echo "chunk $n: TRUNCATED (exit=$R_CODE, partial passed=$R_P) -- retrying per module" \
      | tee -a "$LOG"
    echo "$R_OUT" | tail -15 >> "$LOG"
    RETRIED=$(( RETRIED + 1 ))
    for mod in "${slice[@]}"; do
      run_slice "$mod"
      if [ "$R_TRUNCATED" -eq 1 ]; then
        KILLED=$(( KILLED + 1 )); BAD_CHUNKS=$(( BAD_CHUNKS + 1 ))
        echo "  $mod: KILLED even alone (exit=$R_CODE) -- results unknown" \
          | tee -a "$LOG"
        echo "$R_OUT" | tail -15 >> "$LOG"
      else
        PASS=$(( PASS + R_P )); FAIL=$(( FAIL + R_F )); SKIP=$(( SKIP + R_S ))
        [ "$R_CODE" -ne 0 ] && BAD_CHUNKS=$(( BAD_CHUNKS + 1 ))
        echo "  $mod: exit=$R_CODE passed=$R_P failed=$R_F skipped=$R_S" \
          | tee -a "$LOG"
        [ "$R_F" -ne 0 ] && echo "$R_OUT" \
          | grep -E '^(tests/.+::.+ (FAILED|ERROR)|ERROR tests/)' >> "$LOG"
      fi
      sleep 2
    done
  else
    PASS=$(( PASS + R_P )); FAIL=$(( FAIL + R_F )); SKIP=$(( SKIP + R_S ))
    [ "$R_CODE" -ne 0 ] && BAD_CHUNKS=$(( BAD_CHUNKS + 1 ))
    echo "chunk $n: exit=$R_CODE passed=$R_P failed=$R_F skipped=$R_S" | tee -a "$LOG"
    [ "$R_F" -ne 0 ] && echo "$R_OUT" \
      | grep -E '^(tests/.+::.+ (FAILED|ERROR)|ERROR tests/)' >> "$LOG"
  fi
  # Free the interpreter's memory before the next chunk starts.
  sleep 2

  i=$(( i + CHUNK ))
done

echo "----------------------------------------------------------" | tee -a "$LOG"
echo "TOTAL passed=$PASS failed=$FAIL skipped=$SKIP" | tee -a "$LOG"
echo "bad chunks=$BAD_CHUNKS (of which killed/no-summary=$KILLED)" | tee -a "$LOG"
echo "chunks retried per-module after truncation=$RETRIED" | tee -a "$LOG"

if [ "$BAD_CHUNKS" -ne 0 ]; then
  echo "RESULT: FAIL -- see $LOG" | tee -a "$LOG"
  exit 1
fi
echo "RESULT: PASS -- $PASS passed, $SKIP skipped, 0 failed" | tee -a "$LOG"
