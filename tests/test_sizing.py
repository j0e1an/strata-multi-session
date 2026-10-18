"""First-run sizing: the estimate, the measurement, and the slots a budget holds (spec §9).

Two of these functions exist to agree with ``apply-concurrency-fix.sh``, which is already shipped
and already prints a snapshot size and a slot count. The tests here pin that agreement against the
script's own text, because the only way it survives is being checked: Task 7 prints one source or
the other, and a reader comparing two runs of the same engine must not see two different numbers.
"""

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
    """The shipped script's KV_FACTOR table, as text, for the k8v4 divergence pin."""
    return script_text().split("KV_FACTOR = {")[1].split("}")[0]


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
        # of budget as dead capacity (apply-concurrency-fix.sh:269-272 rounds up for the same
        # reason). An exact multiple must not round up an extra slot.
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
        self.assertIn("0", doc)

    # --- correction 4: what ESTIMATED_CONVERSATION_TOKENS actually is ---

    def test_estimated_tokens_sits_below_the_measured_low_end_by_design(self):
        # 110,000 x 17,406 = 1,826 MiB = 1.78 GiB, under the 1.86 GiB the reference box measured
        # after a day of use. Inverting that range at the measured rate gives ~114,740 and ~136,947
        # tokens, so the constant is below both. Pinned so a future edit cannot quietly start
        # claiming this figure IS the measured length.
        est = recommend.estimate_snapshot_mib(262_144)
        self.assertEqual(est, 1825)
        self.assertLess(est, MEASURED_LOW_MIB)
        # Inverting the range at the measured rate, both ways the figures can be read: exact GiB
        # gives 114,739 / 136,947 tokens (the docstring's numbers), and truncating the range to
        # whole MiB first — the unit these functions speak — gives 114,701 / 136,930.
        self.assertEqual(int(1.86 * 1024 ** 3 // recommend.KV_BYTES_PER_TOKEN_INT8), 114_739)
        self.assertEqual(int(2.22 * 1024 ** 3 // recommend.KV_BYTES_PER_TOKEN_INT8), 136_947)
        tokens_at_low_end = MEASURED_LOW_MIB * MIB // recommend.KV_BYTES_PER_TOKEN_INT8
        self.assertEqual(tokens_at_low_end, 114_701)
        self.assertLess(recommend.ESTIMATED_CONVERSATION_TOKENS, tokens_at_low_end)

    def test_the_constant_is_justified_as_a_fresh_install_figure_not_a_measured_one(self):
        doc = recommend.estimate_snapshot_mib.__doc__
        self.assertIn("fresh", doc.lower())
        self.assertIn("1.86", doc)

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

    def test_expected_tokens_can_be_given_explicitly(self):
        self.assertEqual(recommend.estimate_snapshot_mib(262_144, expected_tokens=262_144),
                         262_144 * 17_406 // MIB)

    # --- the KV scale factors disagree between the two files, on k8v4 ---

    def test_k8v4_scales_on_the_engine_cell_bytes_not_the_scripts_default(self):
        # lib/recommend.py scales k8v4 by the engine's own 816 B/cell against int8's 1056
        # (setup.py:198); apply-concurrency-fix.sh:188-190 has no k8v4 key, so there the format
        # falls through to KV_FACTOR's 1.0 default and reports the int8 size. Same engine, two
        # numbers, on purpose — recorded rather than silently unified.
        k8v4 = recommend.estimate_snapshot_mib(262_144, kv="k8v4")
        int8 = recommend.estimate_snapshot_mib(262_144)
        self.assertEqual(k8v4, int(110_000 * 17_406 * 816 / 1056 // MIB))
        self.assertLess(k8v4, int8)
        self.assertNotEqual(k8v4, int(110_000 * 17_406 * 1.0 // MIB))   # the script's answer

    def test_script_still_has_no_k8v4_entry_so_the_divergence_is_real(self):
        self.assertIn('"int8": 1.0', kv_factor_block())
        self.assertNotIn("k8v4", kv_factor_block())

    def test_the_k8v4_divergence_is_written_where_a_caller_reads_it(self):
        self.assertIn("k8v4", recommend.estimate_snapshot_mib.__doc__)

    # --- spec §14: nothing here may imply it was measured on the caller's box or tier ---

    def test_the_estimate_docstring_names_the_tier_the_rate_was_measured_on(self):
        doc = recommend.estimate_snapshot_mib.__doc__
        self.assertIn("IQ3_S", doc)
        self.assertIn("262144", doc.replace("_", ""))

    def test_the_measured_label_implies_nothing_about_the_tier(self):
        # It is a reading of the caller's own engine, so §14's carry-over warning does not apply to
        # it — but it must not be worded as a property of the model either.
        _, src = recommend.snapshot_from_metrics(12, 12 * 2272 * MIB)
        self.assertEqual(src, "measured from /metrics")


if __name__ == "__main__":
    unittest.main()
