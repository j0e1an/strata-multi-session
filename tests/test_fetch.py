"""fetch-abliterated.sh: the plan it prints, and the shard-2 link it makes.

No test here downloads anything. The network paths are exercised only through --print,
which must be a pure plan, and through small sparse files standing in for the 28.8 GB
shard 2.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

from lib import catalog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "fetch-abliterated.sh")


class TestFetch(unittest.TestCase):
    def setUp(self):
        # A fresh directory per test, not a shared /tmp path: the assertions below are about
        # what the script does NOT create, which a shared path would make order-dependent.
        self.tmp = tempfile.mkdtemp(prefix="strata-fetch-")
        self.target = os.path.join(self.tmp, "does-not-matter")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, *args, **kw):
        # STRATA_DATA/HOME pinned into the temp dir so a real ~/Strata-data on the test box
        # can never be read from, linked into, or accidentally satisfy a link-source search.
        env = dict(os.environ, STRATA_DATA=self.tmp, HOME=self.tmp)
        env.update(kw.pop("env", {}))
        return subprocess.run(["sh", SCRIPT, *args], capture_output=True, text=True, env=env)

    def mark_shard1_done(self, tier):
        """Stand in for a tier whose shard 1 is already fetched and verified."""
        os.makedirs(self.target, exist_ok=True)
        shard1 = catalog.shard_plan(tier)[0]["file"]
        with open(os.path.join(self.target, shard1 + ".done"), "w") as fh:
            fh.write("2026-10-18 00:00")

    def make_sparse(self, path, nbytes):
        with open(path, "wb") as fh:
            fh.truncate(nbytes)

    # ---------------------------------------------------------------- the brief's contract

    def test_unknown_tier_is_rejected_with_the_valid_list(self):
        r = self.run_script("--tier", "Q4_K_M", "--dir", self.target)
        self.assertNotEqual(r.returncode, 0)
        for t in ("IQ3_S", "IQ2_XS"):
            self.assertIn(t, r.stderr + r.stdout)

    def test_print_emits_the_urls_and_expected_sizes_without_touching_disk(self):
        r = self.run_script("--tier", "IQ2_XS", "--dir", self.target, "--print")
        self.assertEqual(r.returncode, 0)
        self.assertIn("39665015072", r.stdout)
        self.assertFalse(os.path.exists(self.target))

    def test_shared_shard2_is_hardlinked_not_downloaded(self):
        r = self.run_script("--tier", "IQ3_XXS", "--dir", self.target, "--print")
        self.assertEqual(r.returncode, 0)
        self.assertIn("link", r.stdout.lower())

    # ------------------------------------------------------- the plan comes from the catalog

    def test_printed_plan_is_the_catalog_for_every_abliterated_tier(self):
        for tier in catalog.abliterated_tiers():
            r = self.run_script("--tier", tier, "--dir", self.target, "--print")
            self.assertEqual(r.returncode, 0, tier)
            for shard in catalog.shard_plan(tier):
                self.assertIn(shard["file"], r.stdout, tier)
                self.assertIn(str(shard["bytes"]), r.stdout, tier)
                self.assertIn(shard["oid"], r.stdout, tier)
                self.assertIn(shard["url"], r.stdout, tier)
            self.assertFalse(os.path.exists(self.target), tier)

    def test_print_needs_no_link_source_and_no_network(self):
        # Nothing under STRATA_DATA, so the only shard-2 source on the box is absent; --print
        # still succeeds, and --refresh is ignored rather than reaching out.
        r = self.run_script("--tier", "Q2_0", "--dir", self.target, "--print", "--refresh")
        self.assertEqual(r.returncode, 0)
        self.assertIn(catalog.shared_shard2()[1], r.stdout)
        self.assertFalse(os.path.exists(self.target))

    # ------------------------------------------------------------------ the shard-2 link

    def test_link_shard2_hardlinks_and_marks_done(self):
        self.mark_shard1_done("IQ3_S")
        nbytes, _ = catalog.shared_shard2()
        source = os.path.join(self.tmp, "stock-shard2.gguf")
        self.make_sparse(source, nbytes)

        r = self.run_script("--tier", "IQ3_S", "--dir", self.target,
                            "--link-shard2", source)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        linked = os.path.join(self.target, catalog.shard_plan("IQ3_S")[1]["file"])
        self.assertEqual(os.path.getsize(linked), nbytes)
        self.assertEqual(os.stat(linked).st_ino, os.stat(source).st_ino)   # a hard link, not a copy
        self.assertTrue(os.path.exists(linked + ".done"))

    def test_link_shard2_wrong_size_is_refused_naming_the_expected_size(self):
        self.mark_shard1_done("IQ3_S")
        source = os.path.join(self.tmp, "wrong-shard2.gguf")
        self.make_sparse(source, 1234)

        r = self.run_script("--tier", "IQ3_S", "--dir", self.target,
                            "--link-shard2", source)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(str(catalog.shared_shard2()[0]), r.stderr + r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.target,
                                                     catalog.shard_plan("IQ3_S")[1]["file"])))

    def test_no_link_source_says_what_to_do_instead_of_failing_in_ln(self):
        self.mark_shard1_done("IQ3_S")
        r = self.run_script("--tier", "IQ3_S", "--dir", self.target)
        self.assertNotEqual(r.returncode, 0)
        out = (r.stderr + r.stdout).lower()
        self.assertIn("--link-shard2", r.stderr + r.stdout)
        self.assertIn("stock", out)
        self.assertFalse(os.path.exists(os.path.join(self.target,
                                                     catalog.shard_plan("IQ3_S")[1]["file"])))

    def test_a_previously_fetched_abliterated_tier_is_a_link_source(self):
        # Shard 2 is byte-identical across the four tiers, so an already-fetched abliterated
        # folder counts as a source without being named on the command line.
        self.mark_shard1_done("IQ3_XXS")
        nbytes, _ = catalog.shared_shard2()
        donor = os.path.join(self.tmp, "models", "IQ3_S-abliterated")
        os.makedirs(donor)
        self.make_sparse(os.path.join(donor, catalog.shard_plan("IQ3_S")[1]["file"]), nbytes)

        r = self.run_script("--tier", "IQ3_XXS", "--dir", self.target)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        linked = os.path.join(self.target, catalog.shard_plan("IQ3_XXS")[1]["file"])
        self.assertEqual(os.path.getsize(linked), nbytes)


if __name__ == "__main__":
    unittest.main()
