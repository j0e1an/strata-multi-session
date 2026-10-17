import unittest
from lib import catalog

class TestCatalog(unittest.TestCase):
    def test_mac_catalog_excludes_uninstallable_tiers(self):
        for t in ("IQ2_XS", "IQ1_M", "NVFP4", "Q8_0", "UD-IQ4_XS"):
            self.assertNotIn(t, catalog.MAC_TIERS)          # metal/setup_mac.py:45 omits these

    def test_abliterated_is_exactly_four_tiers(self):
        self.assertEqual(sorted(catalog.abliterated_tiers()),
                         ["IQ2_XS", "IQ3_S", "IQ3_XXS", "Q2_0"])

    def test_shard2_is_shared_across_abliterated_tiers(self):
        nbytes, oid = catalog.shared_shard2()
        self.assertEqual(nbytes, 28_800_138_432)
        for t in catalog.abliterated_tiers():
            self.assertEqual(catalog.TIERS[t]["shard_bytes"][1], nbytes)
            self.assertEqual(catalog.TIERS[t]["shard_oids"][1], oid)

    def test_every_tier_names_its_sources(self):
        for name, t in catalog.TIERS.items():
            for key in ("family", "download_gb", "ram_gb", "arena_gb", "abliterated"):
                self.assertIn(key, t, name)

    def test_tiers_for_routes_by_os(self):
        self.assertEqual(set(catalog.tiers_for("darwin")), set(catalog.MAC_TIERS))
        self.assertEqual(set(catalog.tiers_for("linux")), set(catalog.TIERS))

    def test_total_bytes_is_the_row_shard_total(self):
        for name, t in catalog.TIERS.items():
            expected = sum(t["shard_bytes"]) if t["shard_bytes"] else None
            self.assertEqual(t["total_bytes"], expected, name)

    def test_download_bytes_covers_the_real_abliterated_files(self):
        for t in catalog.abliterated_tiers():
            quoted = round(catalog.TIERS[t]["download_gb"] * 1_000_000_000)
            self.assertGreaterEqual(catalog.download_bytes(t), quoted, t)
        # 38,021,379,872 + 28,800,138_432: SC117's two files, not upstream's censored 66.4 GB.
        self.assertEqual(catalog.download_bytes("Q2_0"), 66_821_518_304)

    def test_download_bytes_quotes_upstream_for_the_censored_tiers(self):
        self.assertEqual(catalog.download_bytes("Q8_0"), 188_200_000_000)
        # IQ1_M and NVFP4 pin no shard sizes, so upstream's figure is the only honest one there.
        self.assertEqual(catalog.download_bytes("IQ1_M"), 58_400_000_000)
        self.assertEqual(catalog.download_bytes("NVFP4"), 128_000_000_000)

    def test_download_bytes_refuses_to_guess(self):
        self.assertIsNone(catalog.download_bytes("NO-SUCH-TIER"))
        # An abliterated tier with no pinned shards has only the censored figure on hand. Saying
        # nothing beats quoting the size of a file this repo never fetches.
        saved = catalog.TIERS
        catalog.TIERS = dict(saved)
        try:
            catalog.TIERS["IQ9_WIDE"] = {
                "family": "qwen", "download_gb": 71.2, "ram_gb": 64, "arena_gb": 40.0,
                "abliterated": True, "shards": 2, "shard_bytes": [], "shard_oids": [],
                "total_bytes": None,
            }
            self.assertIsNone(catalog.download_bytes("IQ9_WIDE"))
        finally:
            catalog.TIERS = saved
