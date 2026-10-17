import os
import shutil
import subprocess
import tempfile
import unittest

from lib import detect

MEMINFO = "MemTotal:       60810240 kB\nMemFree:         4195120 kB\nMemAvailable:   14625300 kB\n"
SMI = "NVIDIA GeForce RTX 5090, 32607\nNVIDIA GeForce RTX 5090, 32607\n"

# A fixture Strata install, so the venv candidate the pre-flight probes is a path the fixtures
# control rather than the real ~/Strata of whichever box runs the tests.
STRATA = "/fixture/strata"
VENV_PY = os.path.join(STRATA, ".venv", "bin", "python")
HOME_VENV = os.path.expanduser("~/Strata/.venv/bin/python")


def is_probe(cmd: list[str]) -> bool:
    """Is argv one of the two Python pre-flight probes? (routed by the probe text, not argv[0],
    because the two candidates differ only in which interpreter they name)."""
    return len(cmd) > 2 and cmd[1] == "-c" and "ensurepip" in cmd[2]


def py_answers_only(winner: str | None):
    """Fixture box: the pre-flight passes for exactly the interpreter named `winner` (None =
    neither), with a working 2x5090 GPU and 58 GiB of RAM around it."""
    def run(cmd):
        if is_probe(cmd):
            return "ok\n" if cmd[0] == winner else ""
        return SMI if cmd[0] == "nvidia-smi" else MEMINFO
    return run


# The three fixtures the plan pins, spelled out so each test reads as one box.
RADEON = lambda c: MEMINFO if c[0] == "awk" else ""                    # no nvidia-smi
MAC = lambda c: "137438953472" if c[:2] == ["sysctl", "-n"] else ""    # 128 GiB, unified
TWO_5090 = lambda c: SMI if c[0] == "nvidia-smi" else MEMINFO


class TestDetect(unittest.TestCase):
    def test_radeon_is_not_supported_and_says_so(self):
        run = lambda c: MEMINFO if c[0] == "awk" else ""     # nvidia-smi absent
        box = detect.detect(run=run, platform_name="linux")
        self.assertFalse(box["gpu_supported"])
        self.assertIn("NVIDIA", box["gpu_note"])             # names what it does support

    def test_apple_silicon_has_unified_memory(self):
        run = lambda c: "137438953472" if c[:2] == ["sysctl", "-n"] else ""
        box = detect.detect(run=run, platform_name="darwin")
        self.assertEqual(box["ram_mib"], 131072)
        self.assertTrue(box["gpu_supported"])

    def test_vram_sums_across_gpus(self):
        run = lambda c: SMI if c[0] == "nvidia-smi" else MEMINFO
        box = detect.detect(run=run, platform_name="linux")
        self.assertEqual(box["vram_mib"], 65214)             # 32607 x 2, summed, never converted
        self.assertTrue(box["gpu_supported"])
        # The property Task 3's fits() reads: two `*_mib` fields it puts side by side have to be
        # the same unit. The old `== 63` passed with vram in GiB and ram in MiB, which is a 1000x
        # error dressed up as a fit test.
        self.assertGreater(box["vram_mib"], box["ram_mib"] // 10)
        self.assertLess(box["vram_mib"], box["ram_mib"] * 10)

    # --- the rest pins promises the interface makes that the three above leave untested ---

    def test_mac_without_a_memsize_reading_is_not_sized_on_zero(self):
        # gpu_supported False must never carry a note claiming the box was understood.
        box = detect.detect(run=lambda c: "", platform_name="darwin")
        self.assertEqual(box["ram_mib"], 0)
        self.assertEqual(box["vram_mib"], 0)
        self.assertFalse(box["gpu_supported"])
        self.assertIn("NVIDIA", box["gpu_note"])

    def test_apple_silicon_vram_is_the_whole_machine(self):
        box = detect.detect(run=MAC, platform_name="darwin")
        self.assertEqual(box["vram_mib"], box["ram_mib"])    # unified: no separate figure
        self.assertEqual(box["gpus"], ["Apple Silicon"])

    # --- spec §13: "a Python 3.10+ that can make a venv with pip, somewhere usable" ---

    def test_system_python_alone_satisfies_the_preflight(self):
        # The fresh box: no Strata install yet, so only `python3` can answer, and the caller has
        # to learn that it is the system one it will have to build the venv with.
        box = detect.detect(run=py_answers_only("python3"), platform_name="linux", data_dir=STRATA)
        self.assertTrue(box["python_ok"])
        self.assertEqual(box["venv_python"], "python3")

    def test_existing_strata_venv_satisfies_the_preflight(self):
        # The author's box, verified 2026-10-17: system python3 is 3.14.4 with no ensurepip (the
        # Debian/Ubuntu split that makes python3-venv a separate package) and `python3 -m venv`
        # yields a venv with no pip, while ~/Strata/.venv/bin/python is 3.12.15 and passes
        # upstream's probe. Probing python3 alone fires §13's stop on every run on a box that
        # already runs Strata -- which is the box this tool ships to first.
        box = detect.detect(run=py_answers_only(VENV_PY), platform_name="linux", data_dir=STRATA)
        self.assertTrue(box["python_ok"])
        self.assertEqual(box["venv_python"], VENV_PY)

    def test_no_usable_python_leaves_the_caller_no_interpreter_to_name(self):
        box = detect.detect(run=py_answers_only(None), platform_name="linux", data_dir=STRATA)
        self.assertFalse(box["python_ok"])
        self.assertIsNone(box["venv_python"])

    def test_both_candidates_are_asked_upstreams_question_in_upstreams_order(self):
        # Not a new, weaker probe: setup.sh:9's imports and its 3.10 floor, asked of the venv
        # first because that is the interpreter setup.sh reuses when it is there.
        asked = []
        box = detect.detect(run=lambda c: asked.append(c) or "",
                            platform_name="linux", data_dir=STRATA)
        self.assertEqual(asked[:2], [[VENV_PY, "-c", detect.PY_PROBE],
                                     ["python3", "-c", detect.PY_PROBE]])
        self.assertIn("import sys, venv, ensurepip", detect.PY_PROBE)
        self.assertIn("(3, 10)", detect.PY_PROBE)
        self.assertFalse(box["python_ok"])

    def test_existing_venv_counts_on_a_box_that_really_has_one(self):
        # The fixtures prove the seam; this proves the seam is aimed at a real venv. Guarded so a
        # box without that install skips rather than fails.
        if not os.path.exists(HOME_VENV):
            self.skipTest("no ~/Strata/.venv on this box")
        rc = subprocess.run([HOME_VENV, "-c", "import sys, venv, ensurepip; "
                             "sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
                            capture_output=True, text=True).returncode
        if rc != 0:
            self.skipTest("~/Strata/.venv does not pass upstream's probe on this box")
        box = detect.detect(run=detect.default_run, platform_name="linux")
        self.assertTrue(box["python_ok"])
        self.assertEqual(box["venv_python"], HOME_VENV)

    def test_probe_agrees_with_upstreams_own_python_test_on_this_box(self):
        # The fixtures prove the seam; this proves the seam is aimed at the same thing upstream
        # checks (setup.sh:9), whatever this box happens to have installed. data_dir points at a
        # directory that cannot exist, so the venv candidate cannot answer and this compares the
        # system candidate alone -- on a box whose venv is fine and python3 is not, python_ok is
        # True while rc is 1, and that is the whole point of the fallback.
        rc = subprocess.run(["python3", "-c", "import sys, venv, ensurepip; "
                             "sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
                            capture_output=True, text=True).returncode
        no_venv = os.path.join(tempfile.gettempdir(), "strata-no-such-venv-4242")
        box = detect.detect(run=detect.default_run, platform_name="linux", data_dir=no_venv)
        self.assertEqual(box["python_ok"], rc == 0)
        if rc != 0:
            self.assertIsNone(box["venv_python"])            # nothing answered, nothing named

    def test_default_run_turns_any_failure_into_an_empty_string(self):
        # A missing nvidia-smi is the Radeon path, not a traceback -- and the same failure is how
        # a venv candidate that does not exist answers the pre-flight.
        self.assertEqual(detect.default_run(["definitely-not-a-command-4242"]), "")

    def test_both_oses_answer_every_key_the_next_tasks_read(self):
        for platform_name, run in (("linux", RADEON), ("darwin", MAC)):
            box = detect.detect(run=run, platform_name=platform_name)
            self.assertEqual(set(box), {"os", "ram_mib", "memavail_mib", "vram_mib", "gpus",
                                        "gpu_supported", "gpu_note", "cores", "python_ok",
                                        "venv_python"})
            self.assertEqual(box["os"], platform_name)
            self.assertIsInstance(box["gpu_supported"], bool)
            self.assertIsInstance(box["gpus"], list)
            for key in ("ram_mib", "memavail_mib", "vram_mib", "cores"):
                self.assertIsInstance(box[key], int, "%s/%s" % (platform_name, key))
            self.assertGreater(box["cores"], 0)
            # Those fixtures answer "" to every probe, so the honest answer is None, not "".
            self.assertFalse(box["python_ok"])
            self.assertIsNone(box["venv_python"])

    def test_disk_free_mib_is_mib_on_the_path_that_holds_it(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(detect.disk_free_mib(d), shutil.disk_usage(d).free // 1048576)

    def test_disk_free_mib_answers_for_a_directory_that_does_not_exist_yet(self):
        # Task 7 preflights --dir before creating it (the default is ~/Strata-data), so a path
        # that isn't there yet has to be answered, not raised.
        missing = os.path.join(tempfile.gettempdir(), "strata-no-such-dir-4242")
        self.assertEqual(detect.disk_free_mib(missing),
                         detect.disk_free_mib(tempfile.gettempdir()))
