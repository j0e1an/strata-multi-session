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
