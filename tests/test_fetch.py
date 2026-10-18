"""fetch-abliterated.sh: the plan it prints, the shard-2 link it makes, the download it verifies.

No test here downloads anything. The network paths are exercised only through --print,
which must be a pure plan, through small sparse files standing in for the 28.8 GB
shard 2, and through a stub `curl` on PATH (see stub_curl) that writes bytes the test
chooses — so the size check, the real sha256sum and the .done marker all run offline.
"""
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest

from lib import catalog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "fetch-abliterated.sh")

# Every stubbed run here finishes in well under a second; a real curl on a 39 GB shard takes
# minutes. So the cap sits an order of magnitude above the stub and far below a download: a
# test that loses the stub fails in seconds with a message, instead of hanging on the network.
SCRIPT_TIMEOUT_SECONDS = 10


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
        # `script=` runs a scratch copy of the script instead of the shipped one (scratch_repo).
        env = dict(os.environ, STRATA_DATA=self.tmp, HOME=self.tmp)
        env.update(kw.pop("env", {}))
        script = kw.pop("script", SCRIPT)
        try:
            return subprocess.run(["sh", script, *args], capture_output=True, text=True,
                                  env=env, timeout=SCRIPT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self.fail("fetch-abliterated.sh was still running after %ss and was killed: a test "
                      "reached the network (a real curl, not the stub). args: %s"
                      % (SCRIPT_TIMEOUT_SECONDS, " ".join(args)))

    def mark_done(self, filename):
        """Write the <file>.done marker the way setup.py's convention expects."""
        os.makedirs(self.target, exist_ok=True)
        with open(os.path.join(self.target, filename + ".done"), "w") as fh:
            fh.write("2026-10-18 00:00")

    def mark_shard1_done(self, tier):
        """Stand in for a tier whose shard 1 is already fetched and verified."""
        self.mark_done(catalog.shard_plan(tier)[0]["file"])

    def mark_shard2_done(self, tier):
        """Stand in for shard 2 already being in the tree, so pass 1 is a no-op and the
        download branch in pass 2 is the only thing under test."""
        self.mark_done(catalog.shard_plan(tier)[1]["file"])

    # The stub curl: the one way to reach the download branch without a network or 39 GB. It
    # records the arguments it was called with (so the resume/retry flags are testable) and
    # copies a payload the test chose to the -o target. It does not fake the hash — the bytes
    # are the test's, and the script's own sha256sum runs against them.
    STUB_CURL = r"""#!/bin/sh
# Stub curl for the offline download tests in tests/test_fetch.py.
printf '%s\n' "$@" >> "$STUB_CURL_LOG"
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  prev="$a"
done
[ -n "$out" ] || { echo "stub curl: expected an -o TARGET" >&2; exit 22; }
cp "$STUB_CURL_PAYLOAD" "$out"
"""

    def stub_curl(self, payload):
        """Put a stub curl first on PATH that writes `payload`; return (arglog, env)."""
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir, exist_ok=True)
        stub = os.path.join(bindir, "curl")
        with open(stub, "w") as fh:
            fh.write(self.STUB_CURL)
        os.chmod(stub, 0o755)
        src = os.path.join(self.tmp, "payload.bin")
        with open(src, "wb") as fh:
            fh.write(payload)
        arglog = os.path.join(self.tmp, "curl-args.txt")
        env = {"PATH": bindir + os.pathsep + os.environ.get("PATH", ""),
               "STUB_CURL_LOG": arglog, "STUB_CURL_PAYLOAD": src}
        return arglog, env

    def curl_args(self, arglog):
        """The arguments the stub curl was invoked with, as one flat token list."""
        if not os.path.exists(arglog):
            return []
        with open(arglog) as fh:
            return fh.read().split()

    def scratch_repo(self, tier, shard_index, nbytes, oid):
        """Path to a copy of the script whose catalog pins one tiny shard.

        The download tests need a file the real sha256sum can read in microseconds, so the
        catalog's ~39 GB figure is replaced by the stub's payload size. The copy is *appended*
        to the shipped catalog module: shard_plan(), the names and the URLs stay the real ones,
        only the two numbers the stub can honestly satisfy move.
        """
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, "lib"), exist_ok=True)
        shutil.copy(SCRIPT, repo)
        for name in ("__init__.py", "catalog.py"):
            shutil.copy(os.path.join(ROOT, "lib", name), os.path.join(repo, "lib", name))
        with open(os.path.join(repo, "lib", "catalog.py"), "a") as fh:
            fh.write("\n# tests/test_fetch.py: one shard stands in for the real ~39 GB one.\n"
                     "TIERS[%r][\"shard_bytes\"][%d] = %d\n"
                     "TIERS[%r][\"shard_oids\"][%d] = %r\n"
                     % (tier, shard_index, nbytes, tier, shard_index, oid))
        return os.path.join(repo, "fetch-abliterated.sh")

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

    # --------------------------------------------- download, size check, sha check, .done
    #
    # The block that stands between a stranger and a corrupt 60 GB tree: curl, then size, then
    # SHA-256, then the marker. Reaching it needs a curl, so these tests put the stub on PATH
    # and drive it with a payload they choose themselves.

    def test_truncated_download_fails_the_size_check_and_writes_no_done_marker(self):
        # The stub delivers 40 bytes where the catalog pins ~39 GB: a resume that died early.
        # The real catalog is used here, so the message quotes the real expected size.
        shard1 = catalog.shard_plan("IQ2_XS")[0]
        self.mark_shard2_done("IQ2_XS")
        _, env = self.stub_curl(b"x" * 40)

        r = self.run_script("--tier", "IQ2_XS", "--dir", self.target, env=env)
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("SIZE MISMATCH", out)
        self.assertIn(str(shard1["bytes"]), out)
        # Size is checked BEFORE the hash: cheap first, and a truncated file never reaches a
        # hash that would read the whole thing.
        self.assertNotIn("verifying SHA-256", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.target, shard1["file"] + ".done")))

    def test_full_size_wrong_content_fails_the_sha_check_and_writes_no_done_marker(self):
        # The size is exactly what the plan asked for, so the size gate passes and the real
        # sha256sum is what refuses: the shipped oid against bytes that are not the shard.
        shard1 = catalog.shard_plan("IQ2_XS")[0]
        payload = b"exactly the right number of entirely wrong bytes"
        script = self.scratch_repo("IQ2_XS", 0, len(payload), shard1["oid"])
        self.mark_shard2_done("IQ2_XS")
        _, env = self.stub_curl(payload)

        r = self.run_script("--tier", "IQ2_XS", "--dir", self.target, script=script, env=env)
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertNotIn("SIZE MISMATCH", out)
        self.assertIn("SHA-256 MISMATCH", out)
        self.assertIn(shard1["oid"], out)
        self.assertFalse(os.path.exists(os.path.join(self.target, shard1["file"] + ".done")))

    def test_full_size_correct_content_verifies_and_writes_the_done_marker(self):
        # The only honest way to make the hash pass: choose bytes whose real sha256 is the oid
        # the plan expects, and let the script's own sha256sum find it.
        shard1 = catalog.shard_plan("IQ2_XS")[0]
        payload = b"the bytes the stub curl delivered, hashed for real"
        oid = hashlib.sha256(payload).hexdigest()
        script = self.scratch_repo("IQ2_XS", 0, len(payload), oid)
        self.mark_shard2_done("IQ2_XS")
        _, env = self.stub_curl(payload)

        r = self.run_script("--tier", "IQ2_XS", "--dir", self.target, script=script, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("downloaded and verified: " + oid, r.stdout)
        done = os.path.join(self.target, shard1["file"] + ".done")
        self.assertTrue(os.path.exists(done))
        with open(done) as fh:
            self.assertRegex(fh.read(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_download_is_resumable_and_retried(self):
        # -C - and --retry are what let a 51 GiB download survive a dropped connection. They
        # change nothing in the script's output, so the stub's recorded arguments are the only
        # way to see them. The counts are pinned too: `--retry 1 --retry-delay 1` would satisfy
        # a bare assertIn and still give up on the first blip of a two-hour transfer.
        payload = b"resumable, retried, and small enough to hash"
        script = self.scratch_repo("IQ2_XS", 0, len(payload),
                                   hashlib.sha256(payload).hexdigest())
        self.mark_shard2_done("IQ2_XS")
        arglog, env = self.stub_curl(payload)

        r = self.run_script("--tier", "IQ2_XS", "--dir", self.target, script=script, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stdout)
        args = self.curl_args(arglog)
        self.assertIn("-C", args)
        self.assertEqual(args[args.index("-C") + 1], "-")
        self.assertIn("--retry", args)
        self.assertEqual(args[args.index("--retry") + 1], "8")
        self.assertIn("--retry-delay", args)
        self.assertEqual(args[args.index("--retry-delay") + 1], "5")

    def test_shard_already_on_disk_at_the_expected_size_is_verified_without_curl(self):
        # A crash between "curl finished" and "marker written" must not re-fetch: the file on
        # disk is verified instead, and curl is never called.
        shard1 = catalog.shard_plan("IQ2_XS")[0]
        payload = b"already here when the script started"
        script = self.scratch_repo("IQ2_XS", 0, len(payload),
                                   hashlib.sha256(payload).hexdigest())
        self.mark_shard2_done("IQ2_XS")
        arglog, env = self.stub_curl(payload)
        os.makedirs(self.target, exist_ok=True)
        with open(os.path.join(self.target, shard1["file"]), "wb") as fh:
            fh.write(payload)

        r = self.run_script("--tier", "IQ2_XS", "--dir", self.target, script=script, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("already on disk at the expected size", r.stdout)
        self.assertEqual(self.curl_args(arglog), [])
        self.assertTrue(os.path.exists(os.path.join(self.target, shard1["file"] + ".done")))


if __name__ == "__main__":
    unittest.main()
