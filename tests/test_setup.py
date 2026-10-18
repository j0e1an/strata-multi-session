"""bootstrap.sh: the stops, the preflight, and the apply path — all offline.

Why the fixtures look the way they do, in the order measurements on this box forced them:

* **HOME is not overridden**, unlike tests/test_fetch.py. The §13 pre-flight wants an interpreter
  that passes `import sys, venv, ensurepip`, and this box's system python3 3.14.4 fails it
  (``ModuleNotFoundError: No module named 'ensurepip'``, measured 2026-10-18) — only the venv
  under ~/Strata passes. Overriding HOME would fire §13's stop on every run. Instead every test
  passes ``--strata`` at a fixture home whose ``.venv/bin/python`` is a stub that answers the
  probe and then execs python3, so no test reads or writes the real ~/Strata either.
* **A plan's target lives inside the test's own temp dir, and the disk reading is handed to the
  script.** /tmp here is a tmpfs with 8.9 GB free (``df -m /tmp`` -> 8523 available, measured
  2026-10-18) while the tier this box recommends needs 135.2 GB, so a passing plan on a /tmp target
  would refuse by arithmetic rather than by bug. Passing plans therefore pass ``--free-mib`` for a
  figure that is the same on every box; one test omits it and pins the verdict against
  ``shutil.disk_usage`` on the tightest real filesystem instead. Fixed shared target names were
  measured to be unsafe: a mutation run that makes ``--dry-run`` write left ``~/strata-dry-target``
  behind, and the next run failed its own "the target does not exist yet" precondition.
* **Nothing touches 8080, 8090 or 18080** — all three are live here (measured 2026-10-18: a
  connect to each succeeds, 8090 being the llama-swap router this box runs). The router probe is
  aimed at a listener the test opens itself, or at 127.0.0.1:1, which answers ECONNREFUSED here.
  That is enforced by construction, not by every call site remembering: ``run_script`` appends
  ``--router-port 1`` to any invocation that did not name one, because an earlier revision of this
  module claimed the same thing while ``plan()`` never passed the flag and every plan test probed
  the live router on 8090. ``test_no_plan_run_probes_a_dead_router_port`` is the pin. The apply
  tests run apply-concurrency-fix.sh against a config on port 18099, also refused.
* **No test downloads.** The apply path runs against stub fetch-abliterated.sh and
  prepare-abliterated-pack.sh in a scratch repo copy; the config writer and apply-concurrency-fix.sh
  are the real ones.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import unittest

from lib import catalog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "bootstrap.sh")

# A port nothing listens on, so the plan's router probe answers ECONNREFUSED instead of finding
# this box's live llama-swap. run_script() appends it whenever a test did not name its own port.
DEAD_ROUTER_PORT = "1"

# Every stubbed run finishes in seconds; a real fetch is a 55 GB download and a real pack is two
# passes over it. A lost stub therefore fails with a message instead of hanging.
SCRIPT_TIMEOUT_SECONDS = 30
APPLY_TIMEOUT_SECONDS = 120

# The §13 probe answer, then behave like python3 for the lib/ stages.
STUB_VENV_PYTHON = """#!/bin/sh
# tests/test_setup.py: answers bootstrap.sh's §13 probe, then runs the real python3.
for a in "$@"; do
  case "$a" in *ensurepip*) echo ok; exit 0 ;; esac
done
exec python3 "$@"
"""

STUB_FETCH = """#!/bin/sh
# tests/test_setup.py: stands in for fetch-abliterated.sh — no network, no 55 GB.
printf '%s\\n' "$*" >> "$STUB_FETCH_LOG"
dir=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--dir" ]; then dir="$a"; fi
  prev="$a"
done
[ -n "$dir" ] || { echo "stub fetch: expected a --dir" >&2; exit 2; }
mkdir -p "$dir"
: > "$dir/stub-shard-00001-of-00002.gguf.done"
echo "stub fetch: $*"
"""

STUB_PACKER = """#!/bin/sh
# tests/test_setup.py: stands in for prepare-abliterated-pack.sh — no two passes over a shard.
printf 'packer ran for %s\\n' "$STUB_PACK_DIR" >> "$STUB_PACK_LOG"
mkdir -p "$STUB_PACK_DIR"
: > "$STUB_PACK_DIR/native_experts.txt"
echo "stub packer: built $STUB_PACK_DIR"
"""


def _free_gb(path):
    return shutil.disk_usage(path).free / 1e9


def _tightest_parent():
    """The candidate filesystem with the least free space, for the one test that reads reality.

    Adaptive rather than hardcoded so the assertion holds on a box where /tmp is a real disk: the
    test pins the *reading*, whichever direction the arithmetic falls.
    """
    candidates = [tempfile.gettempdir(), os.path.expanduser("~"), ROOT]
    return min(candidates, key=_free_gb)


def shell_only(source):
    """The script's text with every heredoc body removed.

    The bashism scan below has to look at shell lines only: the embedded python stages legitimately
    use ``+=``, which is not a shell operator there.
    """
    kept, delim = [], None
    for line in source.splitlines():
        if delim:
            if line.strip() == delim:
                delim = None
            continue
        kept.append(line)
        match = re.search(r"<<-?'?([A-Za-z_][A-Za-z_0-9]*)'?", line)
        if match:
            delim = match.group(1)
    return "\n".join(kept)


class TestSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="strata-setup-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --------------------------------------------------------------------------- fixtures

    def stub_strata(self, installed=False, name="strata"):
        """A Strata home whose .venv/bin/python answers the §13 probe and then runs python3.

        ``installed=True`` also drops serve/server.py in place, which is how bootstrap.sh decides
        the install stage is already satisfied.

        ``name`` picks the directory because plan()'s default home is an installed one and it is
        built before an explicit ``strata=`` argument is evaluated: a not-installed stub that
        reused the same path would inherit serve/server.py and read as installed.
        """
        home = os.path.join(self.tmp, name)
        bindir = os.path.join(home, ".venv", "bin")
        os.makedirs(bindir, exist_ok=True)     # a test may ask for the same home twice
        stub = os.path.join(bindir, "python")
        with open(stub, "w") as fh:
            fh.write(STUB_VENV_PYTHON)
        os.chmod(stub, 0o755)
        if installed:
            servedir = os.path.join(home, "serve")
            os.makedirs(servedir, exist_ok=True)
            with open(os.path.join(servedir, "server.py"), "w") as fh:
                fh.write("# stub\n")
        return home

    def scratch_repo(self):
        """A copy of the repo whose fetcher and packer are stubs, for the --apply path.

        bootstrap.sh resolves its sibling scripts from its own directory, so the copy is the only
        way to substitute them without touching the shipped files.
        """
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        for name in ("bootstrap.sh", "apply-concurrency-fix.sh"):
            shutil.copy(os.path.join(ROOT, name), repo)
        for name in ("__init__.py", "catalog.py", "detect.py", "recommend.py", "autostart.py",
                     "vision.py"):
            libdir = os.path.join(repo, "lib")
            os.makedirs(libdir, exist_ok=True)
            shutil.copy(os.path.join(ROOT, "lib", name), libdir)
        shutil.copytree(os.path.join(ROOT, "reference"), os.path.join(repo, "reference"))
        for name, body in (("fetch-abliterated.sh", STUB_FETCH),
                           ("prepare-abliterated-pack.sh", STUB_PACKER)):
            path = os.path.join(repo, name)
            with open(path, "w") as fh:
                fh.write(body)
            os.chmod(path, 0o755)
        return repo

    def run_script(self, *args, **kw):
        """Run the entry point. ``shell=`` picks the interpreter, ``script=`` a scratch copy.

        Every run gets ``--router-port`` unless it named one, so no test can reach this box's live
        llama-swap on 8090 by forgetting the flag: the plan's router probe is a side effect of
        printing the plan, and a probe of a live port makes "nothing listening" assertions depend
        on what the box happens to be running.
        """
        shell = kw.pop("shell", "sh")
        script = kw.pop("script", SCRIPT)
        timeout = kw.pop("timeout", SCRIPT_TIMEOUT_SECONDS)
        env = dict(os.environ)
        env.update(kw.pop("env", {}))
        args = list(args)
        if "--router-port" not in args:
            args += ["--router-port", DEAD_ROUTER_PORT]
        argv = [shell, script] + args
        try:
            return subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout)
        except subprocess.TimeoutExpired:
            self.fail("bootstrap.sh was still running after %ss and was killed (a test reached "
                      "the network or a real download): %s" % (timeout, " ".join(argv)))

    def plan(self, *args, **kw):
        """A --dry-run against a target that has room, so the plan itself is what is under test.

        The defaults go first: bootstrap.sh's parser keeps the last value given for a flag, so a
        caller's own --dir/--strata override the defaults rather than the other way round.
        """
        target = kw.pop("target", os.path.join(self.tmp, "plan-target"))
        strata = kw.pop("strata", self.stub_strata(installed=True))
        extra = ["--dir", target, "--strata", strata, "--free-mib", "999999999", "--dry-run"]
        r = self.run_script(*(extra + list(args)), **kw)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def stub_upstream(self):
        """A directory holding a setup.sh, which is all bootstrap.sh checks before it will claim
        stage 1 can run. Never executed by a plan run; the apply tests stop at the install stage."""
        upstream = os.path.join(self.tmp, "upstream")
        os.makedirs(upstream)
        with open(os.path.join(upstream, "setup.sh"), "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(upstream, "setup.sh"), 0o755)
        return upstream

    def read_log(self, path):
        if not os.path.exists(path):
            return ""
        with open(path) as fh:
            return fh.read()

    # ------------------------------------------------------------------ the brief's contract

    def test_dry_run_prints_the_plan_and_writes_nothing(self):
        target = os.path.join(self.tmp, "dry-target")
        self.assertFalse(os.path.exists(target))
        r = self.run_script("--dir", target, "--strata", self.stub_strata(installed=True),
                            "--free-mib", "999999999", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(target),
                         "--dry-run created the target directory")
        for word in ("tier", "sessions", "slots"):
            self.assertIn(word, r.stdout)

    def test_disk_shortfall_refuses_before_downloading(self):
        target = os.path.join(self.tmp, "refused-target")
        r = self.run_script("--dir", target, "--strata", self.stub_strata(installed=True),
                            "--tier", "Q8_0", "--free-mib", "1024", "--dry-run")
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("GB", out)
        self.assertIn("REFUSED", out)
        self.assertFalse(os.path.exists(target), "a refused plan still created its target")

    def test_the_disk_verdict_is_the_real_free_space_reading(self):
        """No --free-mib: the verdict must equal the arithmetic on the actual filesystem.

        Asserted in whichever direction this box is mounted, so the pin is the reading rather than
        a figure that only holds while /tmp is a small tmpfs.
        """
        # Unique per test: the leaf must not exist, and a shared fixed name would let one run's
        # residue decide the next run's verdict.
        probe = "strata-disk-probe-" + os.path.basename(self.tmp)
        target = os.path.join(_tightest_parent(), probe)
        r = self.run_script("--dir", target, "--strata", self.stub_strata(installed=True),
                            "--tier", "Q8_0", "--dry-run")
        # The target does not exist (that is the point); the free space is its parent's, which is
        # also how lib/detect.disk_free_mib answers it.
        free = _free_gb(_tightest_parent())
        required = catalog.download_bytes("Q8_0") / 1e9      # censored: no arena, no shared shard
        if free + 1 < required:
            self.assertNotEqual(r.returncode, 0,
                                "%.1f GB free, %s GB needed, and the plan said ok" % (free, required))
        elif free > required + 1:
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        else:
            self.skipTest("this filesystem sits within 1 GB of Q8_0's requirement (%.1f GB free)"
                          % free)
        self.assertIn("GB", r.stdout + r.stderr)

    def test_unsupported_gpu_stops_with_the_scope_statement(self):
        # --free-mib keeps the disk preflight out of the verdict: with the §12 gate removed this run
        # would exit 0, so the non-zero status below can only come from the GPU stop. Measured:
        # without it, /tmp's 8.4 GB free made the disk refusal the reason and the stop could be
        # deleted without failing the test.
        r = self.run_script("--dir", os.path.join(self.tmp, "gpu-stop"),
                            "--strata", self.stub_strata(installed=True),
                            "--dry-run", "--free-mib", "999999999", "--force-unsupported-gpu")
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("NVIDIA", out)
        self.assertIn("Apple Silicon", out)
        # spec §12: a limitation of this tool, not a claim about Strata.
        self.assertIn("AMD", out)
        # The stop's own paragraph, not lib/detect's note (which also names NVIDIA and AMD).
        folded = " ".join(out.split())
        self.assertIn("Sizing a tier from a vram_mib of 0 would be a guess rather than a fit",
                      folded)
        self.assertIn("stop", out)

    def test_missing_python_stops_with_the_command_not_a_sudo_prompt(self):
        r = self.run_script("--dir", os.path.join(self.tmp, "py-stop"),
                            "--strata", self.stub_strata(),
                            "--dry-run", "--force-no-python")
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("python3-venv", out)
        self.assertIn("Nothing was written", out)
        # spec §13: the one step that can need a password is said plainly and stopped at. The
        # assertion here used to be assertNotIn("sudo will ask", r.stdout) — that string is
        # upstream's message and never this script's, so it could not fail whatever the script
        # did. What can fail is the shape of the remedy: it names the exact command, it says this
        # tool will not run it, and every sudo in the entry point is an echo rather than a command
        # the script executes.
        folded = " ".join(out.split())
        self.assertIn("sudo apt-get install -y python3 python3-venv python3-pip", folded)
        self.assertIn("will not run it for you", folded)
        with open(SCRIPT, encoding="utf-8") as fh:
            shell_lines = shell_only(fh.read()).splitlines()
        for line in shell_lines:
            if "sudo" in line and not line.strip().startswith("#"):
                self.assertTrue(line.strip().startswith("echo"),
                                "sudo is executed rather than printed: %r" % line)

    def test_the_python_stop_never_prints_the_linux_remedy_on_darwin(self):
        """§13's stop routes on the OS: apt/dnf does not exist on a Mac, and upstream's Mac fix
        is a rootless standalone CPython (setup.sh:36-46)."""
        r = self.run_script("--dir", os.path.join(self.tmp, "py-darwin"),
                            "--strata", self.stub_strata(),
                            "--dry-run", "--force-no-python", "--force-os", "darwin")
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        for linux_only in ("python3-venv", "apt-get", "dnf"):
            self.assertNotIn(linux_only, out)
        # No sudo anywhere in the Mac remedy: the whole point of that branch is that upstream
        # resolves the missing interpreter without a password. An earlier assertion here checked a
        # string the script never prints, so it passed whatever this branch said.
        self.assertNotIn("sudo", out)
        self.assertIn("standalone CPython", out)
        self.assertIn("setup.sh:36-46", out)

    # ------------------------------------------------------- the answers a stranger needs

    def test_no_tier_says_upstream_will_still_install_in_the_low_ram_mode(self):
        """A 16 GiB box is not "this machine cannot run the model" (spec §4)."""
        r = self.run_script("--dir", os.path.join(self.tmp, "small-box"),
                            "--strata", self.stub_strata(installed=True),
                            "--dry-run", "--free-mib", "999999999", "--force-ram-mib", "16384")
        self.assertNotEqual(r.returncode, 0)
        # The stop's prose is wrapped, so compare it with the wrapping folded away.
        out = " ".join((r.stdout + r.stderr).split())
        self.assertIn("low-RAM mode", out)
        self.assertIn('"not at a published floor", not "this machine cannot run the model"', out)

    def test_every_ceiling_printed_names_the_flag_that_relieves_it(self):
        r = self.plan()
        ceiling = re.search(r"^ceiling +(\w+)", r.stdout, re.M)
        self.assertIsNotNone(ceiling, r.stdout)
        if ceiling.group(1) != "none":
            relief = re.search(r"^relieve with +(.+)$", r.stdout, re.M)
            self.assertIsNotNone(relief, "ceiling %s printed with no flag" % ceiling.group(1))
            self.assertTrue(relief.group(1).strip().startswith("--"), relief.group(1))

    def test_the_sizing_is_labelled_an_estimate_and_sends_the_user_to_phase_two(self):
        r = self.plan()
        self.assertIn("phase one: an estimate", r.stdout)
        self.assertIn("lib/recommend.estimate_snapshot_mib", r.stdout)
        self.assertIn("apply-concurrency-fix.sh", r.stdout)
        # The measured snapshot moved both ways, so neither direction may be promised.
        self.assertIn("larger or smaller", r.stdout)
        self.assertNotIn("will come back larger", r.stdout)

    def test_the_plan_names_the_abliterated_pack_and_the_stock_pack_it_cannot_reuse(self):
        r = self.plan("--tier", "IQ3_S")
        self.assertIn(os.path.join("packs", "iq3_s_abliterated"), r.stdout)
        self.assertIn("native_experts.txt does not match", r.stdout)

    def test_an_existing_shared_shard2_is_counted_once_not_twice(self):
        """detect() never produces has_shared_shard2, so the orchestrator's scan is what claims
        the 28.8 GB saving spec §16 exists for."""
        target = os.path.join(self.tmp, "shard2-data")
        before = self.plan("--dir", target, "--strata", self.stub_strata(installed=True),
                           "--tier", "IQ3_S")
        need_before = float(re.search(r"^needs +([\d.]+) GB", before.stdout, re.M).group(1))

        ple_bytes = catalog.shared_shard2()[0]
        shard_dir = os.path.join(target, "models", "IQ3_S-abliterated")
        os.makedirs(shard_dir)
        with open(os.path.join(shard_dir, catalog.shard_plan("IQ3_S")[1]["file"]), "wb") as fh:
            fh.truncate(ple_bytes)          # sparse: the scan checks the size, not the content

        after = self.plan("--dir", target, "--strata", self.stub_strata(installed=True),
                          "--tier", "IQ3_S")
        need_after = float(re.search(r"^needs +([\d.]+) GB", after.stdout, re.M).group(1))
        self.assertIn("counted once, not twice", after.stdout)
        self.assertAlmostEqual(need_before - need_after, ple_bytes / 1e9, places=1)

    def test_a_shard2_sized_file_with_the_wrong_name_does_not_claim_the_saving(self):
        target = os.path.join(self.tmp, "shard2-lookalike")
        shard_dir = os.path.join(target, "models", "IQ3_S-abliterated")
        os.makedirs(shard_dir)
        with open(os.path.join(shard_dir, "some-other-28gb-file.gguf"), "wb") as fh:
            fh.truncate(catalog.shared_shard2()[0])
        r = self.plan("--dir", target, "--strata", self.stub_strata(installed=True),
                      "--tier", "IQ3_S")
        self.assertIn("no copy under", r.stdout)
        self.assertNotIn("counted once, not twice", r.stdout)

    def test_the_cpu_encode_curve_is_quoted_on_linux_and_not_on_a_mac(self):
        linux = self.plan()
        self.assertIn("4015 image tokens -> 148 s", linux.stdout)
        # The derived line is what the config writer consumes, so the default is pinned there and
        # not only in the prose: a Mac that defaulted to cpu would print Metal and still write a
        # cpu-encoder config.
        self.assertIn("vision=cpu", linux.stdout)
        mac = self.plan("--force-os", "darwin", "--force-ram-mib", "65536")
        self.assertNotIn("148 s", mac.stdout)
        self.assertIn("Metal", mac.stdout)
        self.assertIn("vision=gpu", mac.stdout)

    def test_the_api_key_caveat_and_the_open_lan_host_are_both_said(self):
        r = self.plan()
        self.assertIn("STRATA_API_KEY", r.stdout)
        self.assertNotIn("open endpoint", r.stdout)          # the default host is loopback
        lan = self.plan("--host", "0.0.0.0")
        self.assertIn("open endpoint", lan.stdout)

    def test_a_mac_is_never_sent_to_an_installer_that_would_refuse_the_tier(self):
        r = self.run_script("--dir", os.path.join(self.tmp, "mac-tier"),
                            "--strata", self.stub_strata(installed=True),
                            "--dry-run", "--free-mib", "999999999",
                            "--force-os", "darwin", "--force-ram-mib", "65536", "--tier", "Q8_0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("MAC_MODELS", r.stdout + r.stderr)

    def test_a_router_is_reported_and_left_alone(self):
        """spec §11: the unit owns serve/server.py, never a router."""
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            r = self.plan("--router-port", str(port))
        self.assertIn("something is listening on 127.0.0.1:%d" % port, r.stdout)
        self.assertIn("will not start, stop or reconfigure", r.stdout)

    def test_no_router_is_reported_as_nothing_listening(self):
        # Port 1 answers ECONNREFUSED here; 8080/8090/18080 are live and none is probed.
        r = self.plan("--router-port", "1")
        self.assertIn("nothing listening on 127.0.0.1:1", r.stdout)

    def test_no_plan_run_probes_a_dead_router_port(self):
        """The hermeticity claim used to be false: plan() never passed --router-port, so every
        plan test probed this box's live llama-swap on 8090 and reported it as a router."""
        r = self.plan()
        self.assertIn("nothing listening on 127.0.0.1:%s" % DEAD_ROUTER_PORT, r.stdout)
        for live in ("8080", "8090", "18080"):
            self.assertNotIn("127.0.0.1:%s" % live, r.stdout,
                             "the plan probed a port that is live on this box")
        # The pin only means something while the script's own default is one of those ports: if
        # the default moved off 8090 the helper's injection would stop being load-bearing, and the
        # assertion above would pass for the wrong reason.
        self.assertIn("which port to probe for a router (default 8090)",
                      self.run_script("--help").stdout)

    # ------------------------------------------- spec §14: carried-over figures say they are one

    def test_a_carried_over_figure_says_it_was_not_measured_on_the_tier(self):
        """§14 obliges the caveat beside every measured figure the tool carries onto another tier.
        The orchestrator prints four of them — the KV rate behind the snapshot estimate, the
        parks-per-session count, the dated parked-snapshot readings and the CPU encode curve — and
        an Unsloth tier has to be told none of them were measured on it."""
        r = self.plan("--tier", "UD-Q6_K_XL")
        folded = " ".join(r.stdout.split())
        self.assertIn("not measured on this tier", folded)
        # Each of the four, not one caveat for the whole screen.
        self.assertIn("estimate: lib/recommend.estimate_snapshot_mib(131072 tokens, --kv int8) "
                      "(carried over", folded)
        self.assertIn("4 sessions x 3 parked each (carried over", folded)
        self.assertIn("1.43 GiB on 2026-10-18 (carried over", folded)
        self.assertIn("measured on the reference box (2x5090, 8 threads, 2026-10-17): (carried over",
                      folded)

    def test_the_measured_family_carries_no_carry_over_caveat(self):
        # The other half: on the family the figures WERE measured on, the caveat must be absent,
        # or it stops being a warning and becomes noise nobody reads.
        r = self.plan("--tier", "IQ3_S")
        self.assertNotIn("not measured on this tier", r.stdout)

    def test_a_forced_tier_outside_the_measured_family_says_so_in_its_own_reason(self):
        # The forced-tier reason is lib's prose now, and it names the rate the ladder is built on.
        r = self.plan("--tier", "UD-Q6_K_XL")
        folded = " ".join(r.stdout.split())
        self.assertIn("at the measured 17,406 bytes/token (that rate is measured on qwen-family "
                      "GSQ-RCO IQ3_S at 262144, not measured on this tier)", folded)

    def test_a_forced_unsloth_tier_does_not_claim_a_mode_its_family_is_exempt_from(self):
        """The prose used to contradict its own arithmetic: the low-RAM sentence was gated on
        fits_in_ram, while the disk figure was gated on low_ram, and Unsloth's budget: True rows
        answer those two tests differently (setup.py:4039-4042 vs :4051). One screen then said the
        mode runs and asked for no arena."""
        r = self.plan("--tier", "UD-Q6_K_XL")
        folded = " ".join(r.stdout.split())
        self.assertIn("exempt from the mode itself", folded)
        self.assertNotIn("upstream's installer will put it in the low-RAM mode", folded)
        self.assertNotIn("low-RAM mode:", folded)
        self.assertNotIn("plus arena", folded)
        # And the figure really is the download alone, which is what the new sentence claims.
        needs = float(re.search(r"^needs +([\d.]+) GB", r.stdout, re.M).group(1))
        self.assertAlmostEqual(needs, catalog.download_bytes("UD-Q6_K_XL") / 1e9, places=1)

    def test_a_tier_the_mode_really_applies_to_still_prints_the_arena(self):
        # Re-gating must not silence the mode where it runs: IQ3_S on this box is under arena_gb +
        # 10 (setup.py:4011-4014), so the sentence and the arena line both stay.
        r = self.plan("--tier", "IQ3_S")
        self.assertIn("low-RAM mode:", r.stdout)
        self.assertIn("plus arena", r.stdout)

    # ------------------------------- the disk preflight, including what stage 1 of --apply fetches

    def test_a_fresh_box_is_asked_for_the_stock_shards_stage_one_fetches(self):
        """The uncensored path still runs upstream's setup.sh (stage 1), and that installer
        downloads the censored build: the engine comes from it, and its shards are the copy
        fetch-abliterated.sh hard-links shard 2 from. Counting only the abliterated download told
        a fresh box ok at 135.2 GB and left ~55 GB for upstream's own check to discover."""
        upstream = self.stub_upstream()
        fresh = self.plan("--tier", "IQ3_S", "--strata",
                          self.stub_strata(installed=False, name="strata-fresh"),
                          "--upstream", upstream)
        installed = self.plan("--tier", "IQ3_S")          # plan()'s default home is installed
        fresh_needs = float(re.search(r"^needs +([\d.]+) GB", fresh.stdout, re.M).group(1))
        installed_needs = float(re.search(r"^needs +([\d.]+) GB", installed.stdout, re.M).group(1))
        stock_non_shared = (round(catalog.TIERS["IQ3_S"]["download_gb"] * 1e9)
                            - catalog.shared_shard2()[0]) / 1e9
        self.assertAlmostEqual(fresh_needs - installed_needs, stock_non_shared, places=1)
        self.assertIn("plus stage 1", fresh.stdout)

    def test_the_disk_section_says_which_stage_one_case_it_printed(self):
        """The figure is the same size in either case only by coincidence, so the section has to
        name the case rather than leave a reader to infer it from a number."""
        upstream = self.stub_upstream()
        fresh = self.plan("--tier", "IQ3_S", "--strata",
                          self.stub_strata(installed=False, name="strata-fresh"),
                          "--upstream", upstream)
        self.assertIn("counted because Strata is not installed", " ".join(fresh.stdout.split()))

        quiet = self.plan("--tier", "IQ3_S")
        self.assertIn("stage 1's stock shards are NOT counted: Strata is already installed",
                      " ".join(quiet.stdout.split()))
        self.assertNotIn("plus stage 1", quiet.stdout)

    def test_a_stock_copy_already_on_disk_is_not_asked_for_again(self):
        target = os.path.join(self.tmp, "stock-held")
        stock_dir = os.path.join(target, "models", "IQ3_S")
        os.makedirs(stock_dir)
        with open(os.path.join(stock_dir, "stock-shard-00001-of-00002.gguf"), "wb") as fh:
            fh.truncate(round(catalog.TIERS["IQ3_S"]["download_gb"] * 1e9))   # sparse
        r = self.plan("--dir", target, "--tier", "IQ3_S", "--strata",
                      self.stub_strata(installed=False, name="strata-fresh"),
                      "--upstream", self.stub_upstream())
        self.assertIn("already holds them", " ".join(r.stdout.split()))
        self.assertNotIn("plus stage 1", r.stdout)

    def test_no_installer_means_no_stock_bytes_and_says_why(self):
        # --apply stops at the install stage when there is no setup.sh, so nothing fetches the
        # stock shards either — and the plan says so instead of going quiet on a smaller number.
        empty = os.path.join(self.tmp, "no-upstream")
        os.makedirs(empty)
        r = self.plan("--tier", "IQ3_S", "--strata",
                      self.stub_strata(installed=False, name="strata-fresh"),
                      "--upstream", empty)
        self.assertNotIn("plus stage 1", r.stdout)
        self.assertIn("no setup.sh at", " ".join(r.stdout.split()))

    # ------------------------------------------- the entry point owns no arithmetic, no constant

    def test_the_entry_point_owns_no_measured_constant_and_no_sizing_arithmetic(self):
        """The header's claim, checked rather than asserted. bootstrap.sh used to carry
        PER_SESSION_PARKS = 3 — a §14-listed measured constant with its provenance in a comment —
        while claiming above it that the file owns no measured figure, and to rebuild the forced
        tier's KV ladder, disk total and budget from lib's constants. All three are lib's now."""
        with open(SCRIPT, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("PER_SESSION_PARKS = ", src,
                         "the measured parks count is back in the entry point")
        self.assertNotIn("0.11", src, "the RAM reserve share is back in the entry point")
        for entry in ("recommend.forced_plan(", "recommend.phase_one_budget(",
                      "recommend.disk_preflight(", "recommend.carry_over_note("):
            self.assertIn(entry, src, "%s is not called from the entry point" % entry)
        # The header states what the file still owns instead of claiming it owns nothing.
        self.assertIn("owns no sizing arithmetic and no", src)
        self.assertIn("two measured READINGS", src)

    # ------------------------------------------------------------------------ the shell itself

    def test_the_script_parses_and_runs_under_dash(self):
        dash = shutil.which("dash")
        if not dash:
            self.skipTest("no dash on this box")
        check = subprocess.run([dash, "-n", SCRIPT], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        target = os.path.join(self.tmp, "dash-target")
        r = self.run_script("--dir", target, "--strata", self.stub_strata(installed=True),
                            "--free-mib", "999999999", "--dry-run", shell=dash)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(target))

    def test_the_shell_lines_hold_no_bashisms(self):
        with open(SCRIPT) as fh:
            src = shell_only(fh.read())
        for token in ("[[", "+=", "local -a", "<(", "declare ", "function ", "echo -e",
                      "let ", "shopt"):
            self.assertNotIn(token, src, "%s is a bashism in a POSIX sh entry point" % token)

    def test_help_documents_every_flag_it_accepts(self):
        r = self.run_script("--help")
        self.assertEqual(r.returncode, 0)
        for flag in ("--dir", "--strata", "--upstream", "--sessions", "--tier", "--abliterated",
                     "--stock", "--vision", "--port", "--host", "--autostart", "--no-autostart",
                     "--refresh", "--explain", "--dry-run", "--apply", "--force-unsupported-gpu",
                     "--force-no-python", "--force-os", "--force-ram-mib", "--free-mib",
                     "--router-port"):
            self.assertIn(flag, r.stdout, flag)

    def test_an_unknown_argument_is_a_usage_error(self):
        r = self.run_script("--not-a-flag")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown argument", r.stderr)

    # --------------------------------------------------------------------------- the apply path

    def apply(self, repo, strata, data, *extra):
        fetch_log = os.path.join(self.tmp, "fetch.log")
        pack_log = os.path.join(self.tmp, "pack.log")
        env = {"STUB_FETCH_LOG": fetch_log, "STUB_PACK_LOG": pack_log,
               "STUB_PACK_DIR": os.path.join(data, "packs", "iq3_s_abliterated")}
        r = self.run_script("--dir", data, "--strata", strata, "--tier", "IQ3_S",
                            "--port", "18099", "--free-mib", "999999999", "--apply", *extra,
                            script=os.path.join(repo, "bootstrap.sh"), env=env,
                            timeout=APPLY_TIMEOUT_SECONDS)
        return r, fetch_log, pack_log

    def config_path(self, strata):
        return os.path.join(strata, "strata-iq3_s-abliterated.json")

    def read_config(self, strata):
        with open(self.config_path(strata)) as fh:
            return json.load(fh)

    def test_apply_writes_the_config_from_the_catalog_and_sizes_it(self):
        repo, strata = self.scratch_repo(), self.stub_strata(installed=True)
        data = os.path.join(self.tmp, "data")
        r, fetch_log, pack_log = self.apply(repo, strata, data)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        # The fetcher is called with the catalog's tier and the plan's model dir.
        self.assertIn("--tier IQ3_S", self.read_log(fetch_log))
        self.assertIn(os.path.join("models", "IQ3_S-abliterated"), self.read_log(fetch_log))
        self.assertIn("packer ran for", self.read_log(pack_log))

        cfg = self.read_config(strata)
        args = [str(a) for a in cfg["args"]]
        shard1 = catalog.shard_plan("IQ3_S")[0]["file"]
        shard2 = catalog.shard_plan("IQ3_S")[1]["file"]
        # shard_plan()'s per-shard key is `file`; a consumer that reads `filename` dies here.
        model_dir = os.path.join(data, "models", "IQ3_S-abliterated")
        self.assertIn(os.path.join(model_dir, shard1), args)
        self.assertIn(os.path.join(model_dir, shard2), args)
        self.assertIn(os.path.join(data, "packs", "iq3_s_abliterated"), args)
        self.assertEqual(args[args.index("--max-context") + 1], "262144")
        self.assertEqual(args[args.index("--kv") + 1], "int8")
        self.assertEqual(cfg["port"], 18099)
        self.assertEqual(cfg["host"], "127.0.0.1")
        self.assertEqual(cfg["cwd"], strata)
        self.assertFalse(cfg["open_browser"])
        # Every path the reference file carries is the reference box's. None may survive.
        self.assertNotIn("/home/YOU", json.dumps(cfg), json.dumps(cfg))
        # The reference box's CUDA lib_dirs do not exist here, so they are dropped, not copied.
        self.assertNotIn("lib_dirs", cfg)
        self.assertFalse(cfg["vision"]["gpu"])
        self.assertEqual(cfg["vision"]["max_tokens"], 1024)

        # apply-concurrency-fix.sh ran as a subprocess and its flags are in the file it rewrote.
        for flag in ("--conversation-cache-mib", "--conversation-cache-slots",
                     "--conversation-cache-min-tokens", "--prompt-cache"):
            self.assertEqual(args.count(flag), 1, flag)
        self.assertTrue(os.path.exists(self.config_path(strata) + ".bak-multisession"))

    def test_apply_is_idempotent_and_re_running_stacks_no_duplicates(self):
        repo, strata = self.scratch_repo(), self.stub_strata(installed=True)
        data = os.path.join(self.tmp, "data")
        first, _, _ = self.apply(repo, strata, data)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second, _, pack_log = self.apply(repo, strata, data)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)

        self.assertIn("exists — left alone", second.stdout)
        self.assertIn("already has native_experts.txt — skipped", second.stdout)
        args = [str(a) for a in self.read_config(strata)["args"]]
        for flag in ("--conversation-cache-mib", "--conversation-cache-slots", "--max-context",
                     "--kv", "--native", "--ple-gguf", "--pack"):
            self.assertEqual(args.count(flag), 1,
                             "%s appears %d times after two applies" % (flag, args.count(flag)))
        # The packer ran once, not twice.
        self.assertEqual(len(self.read_log(pack_log).strip().splitlines()), 1)

    def test_apply_stops_when_strata_and_upstream_are_both_missing(self):
        repo, strata = self.scratch_repo(), self.stub_strata(installed=False)
        data = os.path.join(self.tmp, "data")
        r, _, _ = self.apply(repo, strata, data)
        self.assertNotEqual(r.returncode, 0)
        out = r.stdout + r.stderr
        self.assertIn("setup.sh", out)
        self.assertFalse(os.path.exists(self.config_path(strata)),
                         "a stopped apply still wrote a config")


if __name__ == "__main__":
    unittest.main()
