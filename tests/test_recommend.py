import unittest

from lib import catalog, recommend

# The three boxes the plan pins, with every *_mib in real MiB (Task 2's units fix). BIG is the
# reference box exactly as detect() reads it on this machine (2x RTX 5090 at 32607 MiB each, summed
# as MiB = 65214, beside 60484 MiB of RAM = MemTotal 61,935,996 kB = 59.07 GiB, which is 2.93 GiB
# under IQ3_S's 62 GiB floor and so *tight* — see the module docstring in lib/recommend.py). SMALL
# is a 24 GB gaming box (24.0 GiB of RAM and a 24.0 GiB card), MAC is a 128 GiB Apple Silicon box
# with unified memory.
BIG = {"os": "linux", "ram_mib": 60_484, "vram_mib": 65214, "gpu_supported": True, "cores": 16}
SMALL = {"os": "linux", "ram_mib": 24576, "vram_mib": 24576, "gpu_supported": True, "cores": 8}
MAC = {"os": "darwin", "ram_mib": 131072, "vram_mib": 131072, "gpu_supported": True, "cores": 16}

# Boxes with room beside the experts: 72 GiB and 96 GiB of RAM are over IQ3_S's 62 GiB floor and
# over arena_gb + the 10 GB upstream keeps beside it (50.3 + 10, setup.py:4011-4014), so no arena
# is written and the disk figure is the download alone. The reference box no longer qualifies.
ROOMY = {**BIG, "ram_mib": 73_728}      # 72.0 GiB
ROOMIER = {**BIG, "ram_mib": 98_304}    # 96.0 GiB, also over the 24 GB of room for a cache

# metal/setup_mac.py's whole add_argument set, read at :488-506 on 2026-10-17. The Mac relief must
# never name a flag outside it.
MAC_FLAGS = {"--api-key", "--build", "--check", "--context", "--data-dir", "--draft-vocab",
             "--family", "--gguf-dir", "--host", "--model", "--models-dir", "--no-browser",
             "--no-start", "--port", "--setup", "--update", "--vision", "--yes"}


def relief_flags(answer):
    """The flag names a relief string offers, for the Mac-parser test."""
    return {token for token in answer["relief"].replace("/", " ").split() if token.startswith("--")}


class TestRecommend(unittest.TestCase):
    # --- the seven the plan pins ---

    def test_reference_box_gets_iq3_s_abliterated(self):
        r = recommend.recommend(BIG)
        self.assertEqual(r["tier"], "IQ3_S")
        self.assertTrue(r["abliterated"])

    def test_24gb_box_gets_iq1_m_labelled_tight_and_low_ram(self):
        # The plan pinned this as "no tier"; the corrected units put 24576 MiB at 24.0 GiB, exactly
        # IQ1_M's 32 GB floor less upstream's 8 GB tight band (setup.py:6092), and upstream
        # installs there: low_ram_fits("IQ1_M", 24, 24) is 24 - 6 + 19 >= 23.4 (:4039-4042), which
        # is the "the GPU's VRAM makes up for it (the low-RAM mode)" verdict at :6061. Controller
        # ruling: a setup tool that turns away a runnable machine is the worse error.
        r = recommend.recommend(SMALL)
        self.assertEqual(r["tier"], "IQ1_M")
        self.assertTrue(r["low_ram"])
        self.assertIn("tight", r["reason"])
        self.assertIn("low-RAM mode", r["reason"])

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
        self.assertTrue(low)                                 # 62 GB need against 48.8 GiB RAM

    def test_every_ceiling_names_the_flag_that_relieves_it(self):
        r = recommend.recommend(BIG)
        self.assertTrue(r["relief"].startswith("--"))

    # --- the tight band, which is what the reference box actually is (setup.py:6092, :6111) ---

    def test_the_reference_box_is_tight_and_says_so_with_the_margin(self):
        # 60484 MiB = 59.07 GiB against IQ3_S's 62 GiB floor: upstream's verdict is "tight", not
        # "fits", and the reason must not let a user read it as fits.
        r = recommend.recommend(BIG)
        self.assertIn("tight", r["reason"])
        self.assertIn("2.9 GiB under the floor", r["reason"])   # 62 - 59.07, the margin named

    def test_the_reference_box_runs_iq3_s_in_the_low_ram_mode(self):
        # reference/strata-iq3_s-abliterated.json carries --resident-experts, which upstream writes
        # only from low_ram_resident (setup.py:6329 :6336-6337, emitted :6591). 59.07 GiB is under
        # arena_gb + 10 = 60.3, so the mode applies here, and the disk figure carries the arena.
        r = recommend.recommend(BIG)
        self.assertTrue(r["low_ram"])
        self.assertIn("low-RAM mode", r["reason"])
        self.assertAlmostEqual(r["extra_disk_gb"],
                               catalog.download_bytes("IQ3_S") / 1e9
                               + catalog.TIERS["IQ3_S"]["arena_gb"] + 1, places=1)

    def test_a_box_just_under_the_tight_band_gets_no_tier(self):
        # 24000 MiB = 23.44 GiB, under IQ1_M's floor by 8.56: upstream's verdict is "does not fit",
        # so the band is 8 and not "anything goes".
        r = recommend.recommend({**SMALL, "ram_mib": 24_000})
        self.assertIsNone(r["tier"])
        self.assertIn("does not fit", r["reason"])

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

    def test_no_tier_is_offered_below_the_tight_band(self):
        # 16384 MiB = 16.0 GiB: under every floor by more than the 8 GiB band, so this is the
        # answer that names the floors rather than a degraded tier.
        r = recommend.recommend({"os": "linux", "ram_mib": 16_384, "vram_mib": 24_576,
                                 "gpu_supported": True, "cores": 4})
        self.assertIsNone(r["tier"])
        self.assertEqual(r["alternatives"], [])
        self.assertIsNone(r["extra_disk_gb"])
        self.assertEqual(r["ceiling"], "none")
        self.assertEqual(r["relief"], "")
        self.assertIn("48", r["reason"])                     # the smallest uncensored tier's floor
        self.assertIn("32", r["reason"])                     # IQ1_M's floor, the smallest of any
        self.assertIn("tight", r["reason"])                  # where the stop sits, 8 GiB below

    # --- disk: catalog.download_bytes(), never download_gb, and the shared shard 2 once ---

    def test_disk_figure_is_the_real_abliterated_total_not_the_censored_download_gb(self):
        # ROOMY, not BIG: the reference box is inside the low-RAM mode now, so its figure carries
        # the arena too (pinned by test_the_reference_box_runs_iq3_s_in_the_low_ram_mode). This one
        # is about the download part alone, which needs a box with no arena.
        r = recommend.recommend(ROOMY)
        self.assertAlmostEqual(r["extra_disk_gb"], catalog.download_bytes("IQ3_S") / 1e9, places=1)
        self.assertGreater(r["extra_disk_gb"], catalog.TIERS["IQ3_S"]["download_gb"])

    def test_a_shared_shard2_the_box_already_holds_is_not_counted_twice(self):
        # Shard 2 is byte-identical across all four abliterated tiers, so a user who fetched one
        # tier already has 28.8 GB of the next one (spec 16).
        full = recommend.recommend(BIG)["extra_disk_gb"]
        held = recommend.recommend({**BIG, "has_shared_shard2": True})["extra_disk_gb"]
        self.assertAlmostEqual(full - held, catalog.shared_shard2()[0] / 1e9, places=1)

    def test_low_ram_mode_adds_the_arena_to_the_disk_figure(self):
        # 31471 MiB = 30.73 GiB of RAM: inside IQ1_M's 32 GB floor by 1.27, and under arena_gb +
        # the 10 GB upstream keeps beside the experts (setup.py:4011-4014), so the installer writes
        # an arena to disk.
        r = recommend.recommend({**BIG, "ram_mib": 31_471})
        self.assertEqual(r["tier"], "IQ1_M")
        self.assertTrue(r["low_ram"])
        self.assertAlmostEqual(r["extra_disk_gb"],
                               catalog.download_bytes("IQ1_M") / 1e9
                               + catalog.TIERS["IQ1_M"]["arena_gb"] + 1, places=1)

    def test_a_tier_that_fits_with_room_beside_the_experts_needs_no_arena(self):
        # Re-pointed from BIG, which the corrected units put inside the low-RAM mode (59.07 GiB
        # against arena_gb + 10 = 60.3). 96.0 GiB is 35.7 GiB over the arena, so nothing is mapped.
        r = recommend.recommend(ROOMIER)
        self.assertFalse(r["low_ram"])
        self.assertAlmostEqual(r["extra_disk_gb"], catalog.download_bytes("IQ3_S") / 1e9, places=1)

    # --- fits(): upstream's low_ram_fits, which counts the card (setup.py:4039-4042) ---

    def test_a_small_ram_box_with_a_big_card_fits_in_the_low_ram_mode(self):
        # 40960 MiB = 40.0 GiB of RAM against IQ3_S's 50.3 GB arena: on the RAM alone it is short,
        # but upstream's test is ram - 6 + max(0, vram - 5) >= arena, i.e. 40 - 6 + 19 = 53, and it
        # prints "fits in the low-RAM mode" (:6103). The old RAM-only test said False.
        fits, low = recommend.fits({"os": "linux", "ram_mib": 40_960, "vram_mib": 24_576,
                                    "gpu_supported": True}, catalog.TIERS["IQ3_S"])
        self.assertTrue(fits)
        self.assertTrue(low)

    def test_low_ram_fits_subtracts_the_6_gib_upstream_leaves_out_of_the_way(self):
        # setup.py:4042, the literal in low_ram_fits: ram - 6 + max(0.0, vram_gb - 5) >= arena_gb.
        # 34816 MiB = 34.00 GiB with a 24576 MiB (24.00 GiB) card: 34 - 6 + 19 = 47 < IQ3_S's 50.3
        # GB arena, so upstream refuses. Drop the 6 and it is 34 + 19 = 53 >= 50.3, which answers
        # True. The box is 28 GiB under IQ3_S's 62 GiB floor — well outside the 8 GiB tight band —
        # so this branch alone decides fits(), and the 6 is the only thing keeping the answer False.
        fits, low = recommend.fits({"os": "linux", "ram_mib": 34_816, "vram_mib": 24_576,
                                    "gpu_supported": True}, catalog.TIERS["IQ3_S"])
        self.assertFalse(fits)
        self.assertTrue(low)                                # 34 < arena_gb + 10, so the mode applies

    def test_low_ram_fits_clamps_a_card_under_the_dense_weights_at_zero(self):
        # setup.py:4042: the card's side is max(0.0, vram_gb - 5), so a card smaller than the ~5 GB
        # of dense weights contributes nothing rather than a negative. 30300 MiB = 29.59 GiB with a
        # 4096 MiB (4.00 GiB) card: 29.59 - 6 + 0 = 23.59 >= IQ1_M's 23.4 GB arena, so it fits;
        # without the clamp the card eats 1.0 GiB of the RAM the box does have (22.59 < 23.4).
        # Pinned on _low_ram_fits() itself: fits() also takes the tight-band branch, and 29.59 GiB
        # is inside IQ1_M's 32 GiB floor by less than the 8 GiB band, so fits() answers True whatever
        # the card term does.
        self.assertTrue(recommend._low_ram_fits(recommend._ram_gib(30_300),
                                                recommend._vram_gib(4_096),
                                                catalog.TIERS["IQ1_M"]))

    def test_a_bigger_ram_box_with_a_small_card_does_not_fit(self):
        # 50000 MiB = 48.83 GiB with a 6144 MiB (6.0 GiB) card: 48.83 - 6 + 1 = 43.83 < 50.3, so
        # upstream refuses. What flipped this box from True to False was replacing the old
        # `ram >= tier["arena_gb"]` branch with this reproduction of low_ram_fits — the branch
        # counted the whole arena against the RAM and ignored that the card holds none of it. (The
        # decimal reading alone would not: 52.43 - 6 + 1 = 47.43 is still under 50.3.)
        fits, low = recommend.fits({"os": "linux", "ram_mib": 50_000, "vram_mib": 6_144,
                                    "gpu_supported": True}, catalog.TIERS["IQ3_S"])
        self.assertFalse(fits)
        self.assertTrue(low)

    # --- the two ceilings, and the flag each one relieves ---

    def test_a_short_card_makes_vram_the_ceiling_and_names_max_context(self):
        # 60081 MiB = 58.67 GiB of RAM, inside IQ3_S's 62 GB floor by 3.3 and so tight; a 11444 MiB
        # (11.18 GiB) card cannot hold its expert cache and a context beside it (TUNING.md: the
        # dense side and the draft layer fill the card).
        box = {"os": "linux", "ram_mib": 60_081, "vram_mib": 11_444, "gpu_supported": True,
               "cores": 8}
        r = recommend.recommend(box)
        self.assertEqual(r["tier"], "IQ3_S")
        self.assertEqual(r["ceiling"], "vram")
        self.assertTrue(r["relief"].startswith("--max-context"))
        self.assertLess(r["context"], recommend.TRAINED_CONTEXT)

    def test_a_24gb_card_holds_part_of_the_arena_and_runs(self):
        # Upstream's low_ram_gpu_gb (setup.py:4017-4023) gives a 24576 MiB card 24 - 5 = 19.0 GB of
        # the 50.3 GB arena, so the engine starts; only a card that holds nothing at all is the
        # "no VRAM is left for the expert cache" stop (:5095-5097, :5148).
        r = recommend.recommend({"os": "linux", "ram_mib": 63_488, "vram_mib": 24_576,
                                 "gpu_supported": True, "cores": 8})
        self.assertEqual(r["tier"], "IQ3_S")
        self.assertEqual(r["ceiling"], "vram")
        self.assertNotIn("no VRAM is left for the expert cache", r["reason"])
        self.assertIn("19.0 of the tier's 50.3 GB expert arena", r["reason"])

    def test_a_card_below_the_dense_weights_is_the_hard_stop(self):
        # 4096 MiB = 4.0 GiB, under the ~5 GB of dense weights and buffers upstream counts at
        # :4017-4023: the card holds no experts at any rung, which is the one answer allowed to say
        # the engine stops.
        r = recommend.recommend({"os": "linux", "ram_mib": 63_488, "vram_mib": 4_096,
                                 "gpu_supported": True, "cores": 8})
        self.assertIn("no VRAM is left for the expert cache", r["reason"])

    def test_the_reference_box_keeps_its_trained_context_and_ram_is_the_ceiling(self):
        # 262144 / int8 is what reference/strata-iq3_s-abliterated.json actually runs, and a box
        # already 3.2 GiB under the model's floor has nothing left for a conversation cache
        # (setup.py:5210).
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

    def test_the_24_gb_of_room_beside_the_model_is_what_makes_ram_the_ceiling_on_linux(self):
        # setup.py:5210 (AGENT_CACHE_FREE_GB). Before this, the only no-ceiling test used the MAC
        # fixture, where _ceiling returns "none" on the darwin branch whatever the number is, so
        # setting the constant to 0 or to 96 left the whole suite green. ROOMY is 72.0 GiB against
        # IQ3_S's 62 GiB floor: 10.0 GiB beside the model, under the 24 GB, so RAM binds and the
        # conversation-cache flags are the relief — 0 would report no ceiling at all. ROOMIER is 96.0
        # GiB: 34.0 GiB beside, over it, so nothing binds — 96 would report a RAM ceiling here.
        roomy = recommend.recommend(ROOMY)
        self.assertEqual(roomy["ceiling"], "ram")
        self.assertIn("--conversation-cache-mib", roomy["relief"])
        self.assertIn("--conversation-cache-slots", roomy["relief"])
        self.assertEqual(recommend.recommend(ROOMIER)["ceiling"], "none")

    def test_sessions_size_the_cache_not_the_tier(self):
        self.assertEqual(recommend.recommend(BIG, sessions=1)["tier"],
                         recommend.recommend(BIG, sessions=8)["tier"])

    # --- macOS: relief must be a flag metal/setup_mac.py can actually set (spec 9) ---

    def test_mac_relief_names_only_flags_the_mac_installer_accepts(self):
        # setup_mac.py:488-506 takes --context, not --max-context, and has no --kv and no
        # conversation-cache flag. A 65536 MiB (64.0 GiB) Mac with a 12288 MiB (12.0 GiB) card is
        # VRAM-limited, and the Linux wording for it names two flags that installer cannot set.
        boxes = [MAC,
                 {"os": "darwin", "ram_mib": 65_536, "vram_mib": 12_288, "gpu_supported": True,
                  "cores": 8},
                 {"os": "darwin", "ram_mib": 65_536, "vram_mib": 65_536, "gpu_supported": True,
                  "cores": 8},
                 {"os": "darwin", "ram_mib": 32_768, "vram_mib": 32_768, "gpu_supported": True,
                  "cores": 8}]
        for box in boxes:
            r = recommend.recommend(box)
            for answer in [r, *r["alternatives"]]:
                self.assertTrue(relief_flags(answer) <= MAC_FLAGS,
                                f"{answer['tier']}: {answer['relief']}")
        limited = recommend.recommend(boxes[1])
        self.assertEqual(limited["ceiling"], "vram")
        self.assertEqual(limited["relief"], "--context 8192")
        self.assertNotIn("--max-context", limited["relief"])
        self.assertNotIn("--kv", limited["relief"])

    def test_a_mac_with_a_tight_ram_budget_reports_no_ceiling_it_cannot_relieve(self):
        # 64.0 GiB with IQ3_S leaves 2.0 GiB beside the model, under the 24 GB upstream treats as
        # room for a cache — but the only flags that shrink a cache are Linux's. spec 9: a ceiling
        # that drives no flag is decoration, so it is not reported as one; the reason says it.
        r = recommend.recommend({"os": "darwin", "ram_mib": 65_536, "vram_mib": 65_536,
                                 "gpu_supported": True, "cores": 8})
        self.assertEqual(r["ceiling"], "none")
        self.assertEqual(r["relief"], "")
        self.assertIn("no --conversation-cache flag", r["reason"])

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
