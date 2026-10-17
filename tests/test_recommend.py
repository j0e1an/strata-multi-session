import unittest

from lib import catalog, recommend

# The three boxes the plan pins, with every *_mib in real MiB (Task 2's units fix): BIG is the
# reference box (2x RTX 5090 at 32607 MiB each, summed as MiB = 65214, beside 60240 MiB of RAM),
# SMALL is a 24 GB gaming box, MAC is a 128 GiB Apple Silicon box with unified memory.
BIG = {"os": "linux", "ram_mib": 60240, "vram_mib": 65214, "gpu_supported": True, "cores": 16}
SMALL = {"os": "linux", "ram_mib": 24576, "vram_mib": 24576, "gpu_supported": True, "cores": 8}
MAC = {"os": "darwin", "ram_mib": 131072, "vram_mib": 131072, "gpu_supported": True, "cores": 16}


class TestRecommend(unittest.TestCase):
    # --- the seven the plan pins ---

    def test_reference_box_gets_iq3_s_abliterated(self):
        r = recommend.recommend(BIG)
        self.assertEqual(r["tier"], "IQ3_S")
        self.assertTrue(r["abliterated"])

    def test_24gb_box_gets_no_tier_and_names_the_requirement(self):
        r = recommend.recommend(SMALL)
        self.assertIsNone(r["tier"])
        self.assertIn("48", r["reason"])                     # the smallest tier's RAM floor

    def test_unsupported_gpu_is_not_sized_on_zero_vram(self):
        r = recommend.recommend({**BIG, "gpu_supported": False, "vram_mib": 0})
        self.assertIsNone(r["tier"])
        self.assertIn("NVIDIA", r["reason"])

    def test_mac_never_gets_a_tier_setup_mac_cannot_install(self):
        r = recommend.recommend(MAC)
        self.assertIn(r["tier"], ("Q2_0", "IQ3_XXS", "IQ3_S",
                                  "UD-Q4_K_XL", "UD-Q5_K_XL", "UD-Q6_K_XL"))
        # The alternatives go through the same gate: an alternative setup_mac.py cannot install is
        # as unreachable as a recommendation that it cannot install.
        offered = {r["tier"], *(a["tier"] for a in r["alternatives"])}
        self.assertTrue(offered <= set(catalog.MAC_TIERS), offered - set(catalog.MAC_TIERS))

    def test_coder_tier_reports_it_is_not_abliterated(self):
        r = recommend.recommend({**BIG, "ram_mib": 33_000})
        self.assertEqual(r["tier"], "IQ1_M")
        self.assertFalse(r["abliterated"])
        self.assertIn("censored", r["reason"])

    def test_low_ram_mode_names_its_extra_disk(self):
        fits, low = recommend.fits({**BIG, "ram_mib": 50_000}, catalog.TIERS["IQ3_S"])
        self.assertTrue(fits)
        self.assertTrue(low)                                 # 62 GB need against 49 GB RAM

    def test_every_ceiling_names_the_flag_that_relieves_it(self):
        r = recommend.recommend(BIG)
        self.assertTrue(r["relief"].startswith("--"))

    # --- the stops the constraints demand, which the seven above leave untested ---

    def test_zero_ram_reading_fails_closed_rather_than_recommending(self):
        # A Linux box with no awk reports ram_mib 0 next to gpu_supported True (Task 2). Zero RAM
        # must never produce a confident tier: every tier's floor is above it.
        r = recommend.recommend({**BIG, "ram_mib": 0})
        self.assertIsNone(r["tier"])
        self.assertIn("RAM", r["reason"])
        self.assertEqual(r["alternatives"], [])

    def test_zero_vram_with_a_supported_gpu_is_still_a_stop(self):
        # detect() can answer gpu_supported True with vram_mib 0 when a card reports 0 MiB. Sizing
        # the context off that is the same guess the unsupported-GPU stop exists to prevent.
        r = recommend.recommend({**BIG, "vram_mib": 0})
        self.assertIsNone(r["tier"])
        self.assertIn("VRAM", r["reason"])

    def test_no_tier_is_offered_below_its_published_ram_floor(self):
        # fits() is the permissive "could it run at all" predicate; recommend() holds the tier's
        # published floor, so a box under every floor gets an empty answer, not a degraded one.
        r = recommend.recommend(SMALL)
        self.assertEqual(r["alternatives"], [])
        self.assertIsNone(r["extra_disk_gb"])
        self.assertEqual(r["ceiling"], "none")
        self.assertEqual(r["relief"], "")

    # --- disk: catalog.download_bytes(), never download_gb, and the shared shard 2 once ---

    def test_disk_figure_is_the_real_abliterated_total_not_the_censored_download_gb(self):
        r = recommend.recommend(BIG)
        self.assertAlmostEqual(r["extra_disk_gb"], catalog.download_bytes("IQ3_S") / 1e9, places=1)
        self.assertGreater(r["extra_disk_gb"], catalog.TIERS["IQ3_S"]["download_gb"])

    def test_a_shared_shard2_the_box_already_holds_is_not_counted_twice(self):
        # Shard 2 is byte-identical across all four abliterated tiers, so a user who fetched one
        # tier already has 28.8 GB of the next one (spec 16).
        full = recommend.recommend(BIG)["extra_disk_gb"]
        held = recommend.recommend({**BIG, "has_shared_shard2": True})["extra_disk_gb"]
        self.assertAlmostEqual(full - held, catalog.shared_shard2()[0] / 1e9, places=1)

    def test_low_ram_mode_adds_the_arena_to_the_disk_figure(self):
        # 33.0 GB of RAM: over IQ1_M's 32 GB floor, under arena_gb + the 10 GB upstream keeps
        # beside the experts (setup.py:4011-4014), so the installer writes an arena to disk.
        r = recommend.recommend({**BIG, "ram_mib": 31_471})
        self.assertEqual(r["tier"], "IQ1_M")
        self.assertTrue(r["low_ram"])
        self.assertAlmostEqual(r["extra_disk_gb"],
                               catalog.download_bytes("IQ1_M") / 1e9
                               + catalog.TIERS["IQ1_M"]["arena_gb"] + 1, places=1)

    def test_a_tier_that_fits_with_room_beside_the_experts_needs_no_arena(self):
        r = recommend.recommend(BIG)
        self.assertFalse(r["low_ram"])
        self.assertAlmostEqual(r["extra_disk_gb"], catalog.download_bytes("IQ3_S") / 1e9, places=1)

    # --- the two ceilings, and the flag each one relieves ---

    def test_a_short_card_makes_vram_the_ceiling_and_names_max_context(self):
        # 63 GB of RAM clears IQ3_S's 62 GB floor; a 12 GB card cannot hold its expert cache and a
        # context beside it (TUNING.md: the dense side and the draft layer fill the card).
        box = {"os": "linux", "ram_mib": 60_081, "vram_mib": 11_444, "gpu_supported": True,
               "cores": 8}
        r = recommend.recommend(box)
        self.assertEqual(r["tier"], "IQ3_S")
        self.assertEqual(r["ceiling"], "vram")
        self.assertTrue(r["relief"].startswith("--max-context"))
        self.assertLess(r["context"], recommend.TRAINED_CONTEXT)

    def test_the_reference_box_keeps_its_trained_context_and_ram_is_the_ceiling(self):
        # 262144 / int8 is what reference/strata-iq3_s-abliterated.json actually runs, and 1.2 GB
        # beside a 62 GB model is no room at all for a conversation cache (setup.py:5210).
        r = recommend.recommend(BIG)
        self.assertEqual(r["context"], recommend.TRAINED_CONTEXT)
        self.assertEqual(r["kv"], "int8")
        self.assertEqual(r["ceiling"], "ram")
        self.assertIn("--conversation-cache-mib", r["relief"])
        self.assertIn("--conversation-cache-slots", r["relief"])

    def test_a_box_with_room_beside_the_model_reports_no_ceiling(self):
        r = recommend.recommend(MAC)
        self.assertEqual(r["ceiling"], "none")
        self.assertEqual(r["relief"], "")

    def test_sessions_size_the_cache_not_the_tier(self):
        self.assertEqual(recommend.recommend(BIG, sessions=1)["tier"],
                         recommend.recommend(BIG, sessions=8)["tier"])

    # --- ranking, and spec 14's "not measured on this tier" ---

    def test_alternatives_are_ranked_abliterated_first_then_by_size(self):
        names = [a["tier"] for a in recommend.recommend(BIG)["alternatives"]]
        self.assertEqual(names, ["IQ3_XXS", "IQ2_XS", "Q2_0", "UD-Q4_K_XL", "UD-IQ4_XS", "IQ1_M"])

    def test_figures_are_labelled_when_they_were_not_measured_on_the_recommended_tier(self):
        # spec 14: 17,406 B/token is measured on qwen-family GSQ-RCO IQ3_S at 262144. IQ1_M, the
        # Unsloth tiers and NVFP4 must carry the label, in the text the user sees.
        r = recommend.recommend(BIG, want_abliterated=False)
        self.assertEqual(r["tier"], "UD-Q4_K_XL")
        self.assertIn("not measured on this tier", r["reason"])
        self.assertIn("censored", r["reason"])               # which model it is, stated either way

    def test_the_measured_family_is_not_labelled_as_unmeasured(self):
        self.assertNotIn("not measured on this tier", recommend.recommend(BIG)["reason"])

    def test_an_uncensored_answer_still_names_the_uncensored_tiers_it_skipped(self):
        r = recommend.recommend({**BIG, "ram_mib": 33_000})
        for tier in catalog.abliterated_tiers():
            self.assertIn(tier, r["reason"])

    # --- the contract Task 7 prints ---

    def test_every_answer_carries_the_keys_task_7_prints(self):
        keys = {"tier", "abliterated", "reason", "context", "kv", "low_ram", "extra_disk_gb",
                "ceiling", "relief", "alternatives"}
        for box in (BIG, SMALL, MAC, {**BIG, "gpu_supported": False, "vram_mib": 0}):
            r = recommend.recommend(box)
            self.assertEqual(set(r), keys)
            self.assertIn(r["ceiling"], ("vram", "ram", "none"))
            self.assertIsInstance(r["alternatives"], list)
            for alt in r["alternatives"]:
                self.assertEqual(set(alt), keys)
                self.assertEqual(alt["alternatives"], [])   # the ranking lives on the top answer
            if r["ceiling"] != "none":
                self.assertTrue(r["relief"].startswith("--"), r["reason"])

    def test_a_box_with_no_gpu_key_is_treated_as_unsupported(self):
        # Fail closed on a missing key too: detect() always answers it, but a hand-built box dict
        # must not become a recommendation by omission.
        self.assertIsNone(recommend.recommend({"os": "linux", "ram_mib": 60240})["tier"])


if __name__ == "__main__":
    unittest.main()
