"""First-run sizing: the estimate, the measurement, and the slots a budget holds (spec §9).

Two of these functions exist to agree with ``apply-concurrency-fix.sh``, which is already shipped
and already prints a snapshot size and a slot count. The cross-file pins here read the script's own
text — the measured label, the fallback figure and the KV_FACTOR table — because the only way that
agreement survives is being checked: Task 7 prints one source or the other, and a reader comparing
two runs of the same engine must not see two different numbers. The arithmetic itself (the 17,406
B/token rate, the truncating division, the ceil) is pinned to literals copied from the script's
lines, so a mutation of the script re-checks only what is read from it.
"""

import ast
import os
import unittest

from lib import recommend

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "apply-concurrency-fix.sh")

MIB = 1_048_576

# spec §9's measured parked-snapshot range on the reference box: 1.86 GiB average at 15:00 and
# 2.22 GiB at 21:00 the same day, live /metrics. In MiB, the unit these functions speak.
MEASURED_LOW_MIB = int(1.86 * 1024)     # 1904
MEASURED_HIGH_MIB = int(2.22 * 1024)    # 2273


def script_text():
    with open(SCRIPT, encoding="utf-8") as f:
        return f.read()


def kv_factor_block():
    """The shipped script's KV_FACTOR table, as text, for the KV divergence pins."""
    return script_text().split("KV_FACTOR = {")[1].split("}")[0]


def kv_factor_table():
    """The same table parsed. Parsed, not substring-matched: ``"q4_0": 0.545`` contains
    ``"q4_0": 0.5``, so a substring pin would let the script catch up to lib and still read as a
    divergence."""
    return ast.literal_eval("{" + kv_factor_block() + "}")


def upstream_setup_lines(first, last):
    """Lines ``first``-``last`` (1-indexed) of upstream's setup.py, or None if it isn't here.

    Upstream isn't vendored in this repo; the repo's own scripts reach it as
    ``${STRATA_HOME:-$HOME/Strata}`` (prepare-abliterated-pack.sh:13-14), so the pin follows that
    convention — STRATA_HOME overrides it, which is also how the pin is tested against a mutated
    copy — and skips on a box without upstream, as tests/test_detect.py:133 does for ~/Strata/.venv.
    """
    root = os.environ.get("STRATA_HOME") or os.path.expanduser("~/Strata")
    path = os.path.join(root, "setup.py")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.readlines()[first - 1:last]


class TestSizing(unittest.TestCase):
    # --- the four the brief pins ---

    def test_estimate_is_below_the_raw_worst_case(self):
        est = recommend.estimate_snapshot_mib(262_144)
        worst = 262_144 * 17_406 // 1_048_576
        self.assertLess(est, worst)
        self.assertEqual(est, 110_000 * 17_406 // 1_048_576)

    def test_metrics_reading_is_labelled_measured(self):
        mib, src = recommend.snapshot_from_metrics(12, 12 * 2272 * 1_048_576)
        self.assertEqual(mib, 2272)
        self.assertTrue(src.startswith("measured"))

    def test_no_parks_is_labelled_estimate_not_measured(self):
        mib, src = recommend.snapshot_from_metrics(0, 0)
        self.assertFalse(src.startswith("measured"))

    def test_slots_round_up_so_budget_and_slots_agree(self):
        self.assertEqual(recommend.slots_for_budget(30_720, 2272), 14)
        self.assertGreaterEqual(recommend.slots_for_budget(30_720, 2272) * 2272, 30_720)

    # --- correction 1: slots_for_budget answers "how many slots", not "what budget" ---

    def test_slots_is_the_smallest_count_that_covers_the_budget(self):
        # ceil, not floor: at 30,720 MiB and 2,272 MiB a floor gives 13 slots and leaves 1,184 MiB
        # of budget as dead capacity (apply-concurrency-fix.sh:272 rounds up for the same reason,
        # its comment at :269-271 says why). An exact multiple must not round up an extra slot.
        for budget, snap in ((30_720, 2272), (4_544, 2272), (2_272, 2272), (2_273, 2272), (1, 2272)):
            slots = recommend.slots_for_budget(budget, snap)
            self.assertGreaterEqual(slots * snap, budget, (budget, snap, slots))
            self.assertLess((slots - 1) * snap, budget, (budget, snap, slots))

    def test_slots_does_not_fold_in_the_parked_per_session_factor(self):
        # The script sizes a BUDGET as sessions x per_session x snapshot, with per_session = 3 at
        # :241-243 and the multiply at :251. slots_for_budget takes no session count, so folding in
        # the 3 would answer the script's question and not this one — 5 slots where 14 fit.
        self.assertEqual(recommend.slots_for_budget(30_720, 2272), -(-30_720 // 2272))
        self.assertNotEqual(recommend.slots_for_budget(30_720, 2272), -(-30_720 // (3 * 2272)))

    def test_slots_never_drops_below_one(self):
        # The script's max(1, ...) at :272: a budget smaller than one snapshot still gets a slot.
        self.assertEqual(recommend.slots_for_budget(0, 2272), 1)
        self.assertEqual(recommend.slots_for_budget(100, 2272), 1)

    def test_a_negative_budget_gets_the_scripts_floor_not_a_stop(self):
        # Ruling, pinned: a negative budget is not meaningful input — the script's own comes from
        # min(need_mib, ram_allowance_mib) at :255 with the allowance floored at 0 at :254, so it
        # cannot go negative — and the script's max(1, ...) at :272 answers 1 for it. This mirrors
        # that rather than inventing a stop the script lacks, so -5,000 is the same answer as 0.
        self.assertEqual(recommend.slots_for_budget(-5000, 2272), 1)
        self.assertEqual(recommend.slots_for_budget(-5000, 2272), recommend.slots_for_budget(0, 2272))
        self.assertIn("negative", recommend.slots_for_budget.__doc__)

    def test_slots_with_no_snapshot_size_is_a_stop_not_a_number(self):
        # snapshot_from_metrics returns 0 exactly when no measurement exists. Sizing slots from
        # that is the guess spec §12 forbids, so it refuses rather than inventing a count.
        with self.assertRaises(ValueError) as cm:
            recommend.slots_for_budget(30_720, 0)
        self.assertIn("estimate_snapshot_mib", str(cm.exception))

    def test_slots_docstring_names_the_scripts_budget_sizing_as_a_different_computation(self):
        doc = recommend.slots_for_budget.__doc__
        self.assertIn("per_session", doc)
        self.assertIn("ceil", doc)

    # --- correction 2: the measured path reproduces the script's arithmetic and its label ---

    def test_measured_label_is_the_scripts_string_verbatim(self):
        _, src = recommend.snapshot_from_metrics(12, 12 * 2272 * MIB)
        self.assertEqual(src, "measured from /metrics")
        # The script still prints that exact string (:210). If it ever changes, this test says the
        # two labels have to be re-synced rather than letting them drift apart silently.
        self.assertIn('"measured from /metrics"', script_text())

    def test_measured_arithmetic_is_the_scripts_truncating_division(self):
        # int(bytes / parked / 1048576) — truncating, not rounding: 5,701 MiB across 3 parks is
        # 1900.33 and the script reports 1900 (:206).
        self.assertEqual(recommend.snapshot_from_metrics(3, 5701 * MIB),
                         (1900, "measured from /metrics"))

    def test_measured_reading_of_the_reference_box_range(self):
        # The same arithmetic the box produced at 15:00 and 21:00 (spec §9), so the range this file
        # sizes against is reproducible through the function Task 7 will call.
        self.assertEqual(recommend.snapshot_from_metrics(4, int(1.86 * 1024 * 4 * MIB))[0], 1904)
        self.assertEqual(recommend.snapshot_from_metrics(4, int(2.22 * 1024 * 4 * MIB))[0], 2273)

    # --- correction 3: the zero is a value the script never treats as a size ---

    def test_zero_park_is_a_zero_the_caller_must_not_size_from(self):
        # The brief's label, verbatim, and the value pinned too: 0 is not a snapshot size.
        self.assertEqual(recommend.snapshot_from_metrics(0, 0),
                         (0, "estimate: engine has parked nothing yet"))

    def test_zero_bytes_is_not_a_measurement_either(self):
        # The script's own guard is `if c.get("parked") and c.get("bytes")` (:205) — it falls
        # through on either being empty, so a park count with no bytes is not a measurement here
        # either, and must not come back labelled "measured".
        mib, src = recommend.snapshot_from_metrics(5, 0)
        self.assertEqual(mib, 0)
        self.assertFalse(src.startswith("measured"))

    def test_the_zero_contract_is_written_where_a_caller_reads_it(self):
        doc = recommend.snapshot_from_metrics.__doc__
        self.assertIn("estimate_snapshot_mib", doc)
        # Not assertIn("0", doc): "0" appears in :205, :208, 1048576 and 2,560 whether or not the
        # contract paragraph is still there. This substring can only match the contract itself.
        self.assertIn("The zero is not a size", doc)

    def test_the_scripts_fallback_snapshot_size_lives_in_lib_as_a_number(self):
        # Task 7 has to name the script's last-resort figure when it has nothing else, and a second
        # hardcoded 2560 in the printer is exactly the drift the plan's "numbers live in lib/" rule
        # prevents. Read off the script's own fall-through line (:208) rather than quoted in prose,
        # so changing it there fails here instead of leaving lib's prose describing a number the
        # script no longer uses.
        line = next(l for l in script_text().splitlines()
                    if l.startswith("snapshot_mib = want_snap or"))
        self.assertEqual(int(line.rsplit(" or ", 1)[1]), recommend.FALLBACK_SNAPSHOT_MIB)
        # The branch the constant names still exists and still labels itself a fallback (:212).
        self.assertIn('"fallback 2.5 GiB"', script_text())
        self.assertIn("FALLBACK_SNAPSHOT_MIB", recommend.snapshot_from_metrics.__doc__)

    # --- correction 4: what ESTIMATED_CONVERSATION_TOKENS actually is ---

    def test_estimated_tokens_sits_below_the_measured_low_end_by_design(self):
        # 110,000 x 17,406 = 1,825 MiB = 1.78 GiB (1,914,660,000 / 1,048,576 = 1825.96, truncated),
        # under the 1.86 GiB the reference box measured on 2026-10-17 after a day of use. Inverting
        # that range at the measured rate gives 114,739 and 136,947 tokens, so the constant is below
        # both. Pinned so a future edit cannot quietly start claiming this figure IS the measured
        # length — and note it is only below the 2026-10-17 readings: the 2026-10-18 ones (1.43 and
        # 1.62 GiB) sit under it, which is the half the docstring has to carry too.
        est = recommend.estimate_snapshot_mib(262_144)
        self.assertEqual(est, 1825)
        self.assertLess(est, MEASURED_LOW_MIB)
        # Inverting the range at the measured rate, both ways the figures can be read: exact GiB
        # gives 114,739 / 136,947 tokens (the docstring's numbers), and truncating the range to
        # whole MiB first — the unit these functions speak — gives 114,701 / 136,930.
        self.assertEqual(int(1.86 * 1024 ** 3 // recommend.KV_BYTES_PER_TOKEN_INT8), 114_739)
        self.assertEqual(int(2.22 * 1024 ** 3 // recommend.KV_BYTES_PER_TOKEN_INT8), 136_947)
        tokens_at_low_end = MEASURED_LOW_MIB * MIB // recommend.KV_BYTES_PER_TOKEN_INT8
        tokens_at_high_end = MEASURED_HIGH_MIB * MIB // recommend.KV_BYTES_PER_TOKEN_INT8
        self.assertEqual(tokens_at_low_end, 114_701)
        self.assertEqual(tokens_at_high_end, 136_930)
        self.assertLess(recommend.ESTIMATED_CONVERSATION_TOKENS, tokens_at_low_end)

    def test_the_constant_is_a_fresh_install_figure_bracketed_by_readings_not_a_measured_one(self):
        doc = recommend.estimate_snapshot_mib.__doc__
        self.assertIn("fresh", doc.lower())
        self.assertIn("1.86", doc)
        # The direction claim has to be falsifiable both ways: the docstring names the lower
        # 2026-10-18 readings beside spec §9's, so it cannot read as "phase two comes back larger".
        self.assertIn("2026-10-18", doc)
        self.assertIn("larger or smaller", doc)

    def test_the_estimate_is_the_2_3x_less_pessimistic_figure_spec_9_asks_for(self):
        worst = 262_144 * 17_406 // MIB
        self.assertEqual(worst, 4351)                     # 4.25 GiB, spec §9's theoretical figure
        self.assertAlmostEqual(worst / recommend.estimate_snapshot_mib(262_144), 2.38, places=2)

    # --- the context window is the ceiling on a conversation, so it caps the estimate ---

    def test_estimate_is_capped_by_the_context_window(self):
        # A parked snapshot holds one conversation's KV, and a conversation cannot outlive the
        # window it was generated in. Sizing a 32K box from 110,000 tokens would over-provision it
        # 3.4x, which is the same class of error as the 4.25 GiB worst case, in the other direction.
        self.assertEqual(recommend.estimate_snapshot_mib(32_768), 32_768 * 17_406 // MIB)
        self.assertEqual(recommend.estimate_snapshot_mib(32_768, expected_tokens=200_000),
                         32_768 * 17_406 // MIB)
        for ctx in (8192, 32_768, 131_072, 262_144):
            self.assertLessEqual(recommend.estimate_snapshot_mib(ctx), int(ctx * 17_406 / MIB))

    def test_estimate_with_no_context_is_not_a_size(self):
        # The script's own `if max_context else 0` (:191): no context configured means no figure,
        # not a free one.
        self.assertEqual(recommend.estimate_snapshot_mib(0), 0)

    def test_expected_tokens_of_zero_is_a_documented_zero_not_a_stop(self):
        # Ruling, pinned: expected_tokens 0 (or negative) parks nothing, so the figure is 0 — the
        # same documented zero as context 0, not an exception. The `tokens <= 0` guard is dead for
        # 0 itself (0 * rate // MIB is already 0), so the behaviour is what the docstring promises
        # rather than what the branch happens to do. slots_for_budget refuses a 0, so a caller
        # cannot turn this zero into a slot count by accident.
        self.assertEqual(recommend.estimate_snapshot_mib(262_144, expected_tokens=0), 0)
        self.assertEqual(recommend.estimate_snapshot_mib(262_144, expected_tokens=-1000), 0)
        with self.assertRaises(ValueError):
            recommend.slots_for_budget(30_720,
                                       recommend.estimate_snapshot_mib(262_144, expected_tokens=0))
        # And the choice is written where a caller reads it, not only in the code.
        self.assertIn("expected_tokens`` is 0 or negative", recommend.estimate_snapshot_mib.__doc__)

    def test_expected_tokens_can_be_given_explicitly(self):
        self.assertEqual(recommend.estimate_snapshot_mib(262_144, expected_tokens=262_144),
                         262_144 * 17_406 // MIB)

    # --- the KV scale factors disagree between the two files, on k8v4 AND q4_0 ---

    def test_kv_formats_scale_on_the_engine_cell_bytes_not_the_scripts_factors(self):
        # lib/recommend.py scales every format by the engine's own per-cell bytes — 816 B/cell for
        # k8v4 and 576 for q4_0 against int8's 1056 (setup.py:198 for 816 and 576, setup.py:197 for
        # int8's 1056; pinned against upstream by the test below). apply-concurrency-fix.sh:188-190
        # disagrees on two of the three, in two different ways: it has NO k8v4 key, so
        # KV_FACTOR.get(kv, 1.0) at :190 falls through to the int8 factor; and it has a q4_0 key
        # holding 0.5 where the engine's cells give 576/1056 = 0.545. Same engine, two numbers, on
        # purpose — recorded rather than silently unified.
        int8 = recommend.estimate_snapshot_mib(262_144)
        self.assertEqual(int8, 110_000 * 17_406 // MIB)

        # k8v4: missing key on the script's side, so its answer is the 1.0 default — the int8 size.
        # lib is the smaller of the two here.
        k8v4 = recommend.estimate_snapshot_mib(262_144, kv="k8v4")
        script_k8v4 = int(110_000 * 17_406 * 1.0 // MIB)
        self.assertEqual(k8v4, int(110_000 * 17_406 * 816 / 1056 // MIB))
        self.assertLess(k8v4, script_k8v4)
        self.assertAlmostEqual((script_k8v4 - k8v4) / script_k8v4, 0.227, places=3)

        # q4_0: both files have a key and the numbers simply differ, and lib is the LARGER one —
        # the gap runs the other way from k8v4's, which is why the prose names them separately.
        q4_0 = recommend.estimate_snapshot_mib(262_144, kv="q4_0")
        script_q4_0 = int(110_000 * 17_406 * 0.5 // MIB)
        self.assertEqual(q4_0, int(110_000 * 17_406 * 576 / 1056 // MIB))
        self.assertGreater(q4_0, script_q4_0)
        self.assertAlmostEqual((q4_0 - script_q4_0) / script_q4_0, 0.091, places=3)

        # The same pair at the script's own whole-window basis, which is what the docstring quotes
        # (2,373 MiB against 2,175 MiB at 262,144) — the gap is the rate's, not the basis's.
        self.assertEqual(recommend.estimate_snapshot_mib(262_144, expected_tokens=262_144,
                                                         kv="q4_0"), 2373)
        self.assertEqual(int(262_144 * 17_406 * 0.5 // MIB), 2175)

    def test_the_engine_still_publishes_the_per_cell_bytes_the_prose_quotes(self):
        # The other half of the tripwire. Without it the pin is one-sided: it only ever asserted
        # that the script still lacks a k8v4 key, so upstream moving 816 would leave the suite green
        # while the prose's "the engine's own 816 B/cell" went false. setup.py:198 is the dict,
        # setup.py:197 is the comment int8's 1056 lives in.
        lines = upstream_setup_lines(197, 198)
        if lines is None:
            self.skipTest("no upstream setup.py at ${STRATA_HOME:-$HOME/Strata} on this box")
        comment, assignment = lines
        self.assertIn("KV_CELL_BYTES = {", assignment)          # the citation still points at :198
        self.assertEqual(ast.literal_eval(assignment.split("= ", 1)[1]),
                         {"q4_0": recommend.KV_CELL_BYTES["q4_0"],
                          "k8v4": recommend.KV_CELL_BYTES["k8v4"]})
        self.assertIn(f"8-bit {recommend.KV_CELL_BYTES['int8']}", comment)   # int8's 1056, at :197

    def test_scripts_kv_factor_table_still_diverges_in_both_ways(self):
        # The script side of the same divergence, read from its own text: int8 agrees, k8v4 has no
        # key so the 1.0 default applies, q4_0 has a key with a different number. Parsed rather than
        # substring-matched — `"q4_0": 0.545` contains `"q4_0": 0.5`, so a substring pin would let
        # the script catch up to lib and the prose would still claim a gap that had closed.
        table = kv_factor_table()
        self.assertEqual(table["int8"], 1.0)
        self.assertNotIn("k8v4", table)
        self.assertEqual(table["q4_0"], 0.5)

    def test_the_kv_divergence_is_written_where_a_caller_reads_it(self):
        doc = recommend.estimate_snapshot_mib.__doc__
        self.assertIn("k8v4", doc)
        self.assertIn("q4_0", doc)          # both divergent formats, not one
        self.assertIn("missing key", doc)   # and the prose says which kind of gap each one is
        self.assertIn("disagree on the number", doc)

    # --- spec §14: nothing here may imply it was measured on the caller's box or tier ---

    def test_the_estimate_docstring_names_the_tier_the_rate_was_measured_on(self):
        doc = recommend.estimate_snapshot_mib.__doc__
        self.assertIn("IQ3_S", doc)
        self.assertIn("262144", doc.replace("_", ""))


if __name__ == "__main__":
    unittest.main()
