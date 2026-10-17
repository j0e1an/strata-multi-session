import os
import shutil
import subprocess
import tempfile
import unittest

from lib import detect

MEMINFO = "MemTotal:       60810240 kB\nMemFree:         4195120 kB\nMemAvailable:   14625300 kB\n"
SMI = "NVIDIA GeForce RTX 5090, 32607\nNVIDIA GeForce RTX 5090, 32607\n"

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
        self.assertEqual(box["vram_mib"], 63)
        self.assertTrue(box["gpu_supported"])

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

    def test_no_python_is_reported_through_the_same_seam(self):
        # spec §13. Debian/Ubuntu ship `venv` without `ensurepip` (the separate python3-venv
        # package), and that box is indistinguishable from a good one until setup.sh asks for
        # a password -- so the probe has to be reachable from a fixture.
        no_ensurepip = lambda c: "" if c[0] == "python3" else TWO_5090(c)
        self.assertFalse(detect.detect(run=no_ensurepip, platform_name="linux")["python_ok"])
        good = lambda c: "ok\n" if c[0] == "python3" else TWO_5090(c)
        self.assertTrue(detect.detect(run=good, platform_name="linux")["python_ok"])

    def test_probe_agrees_with_upstreams_own_python_test_on_this_box(self):
        # The fixtures prove the seam; this proves the seam is aimed at the same thing upstream
        # checks (setup.sh:9), whatever this box happens to have installed.
        rc = subprocess.run(["python3", "-c", "import sys, venv, ensurepip; "
                             "sys.exit(0 if sys.version_info >= (3, 10) else 1)"],
                            capture_output=True, text=True).returncode
        box = detect.detect(run=detect.default_run, platform_name="linux")
        self.assertEqual(box["python_ok"], rc == 0)

    def test_default_run_turns_any_failure_into_an_empty_string(self):
        # A missing nvidia-smi is the Radeon path, not a traceback.
        self.assertEqual(detect.default_run(["definitely-not-a-command-4242"]), "")

    def test_both_oses_answer_every_key_the_next_tasks_read(self):
        for platform_name, run in (("linux", RADEON), ("darwin", MAC)):
            box = detect.detect(run=run, platform_name=platform_name)
            self.assertEqual(set(box), {"os", "ram_mib", "memavail_mib", "vram_mib", "gpus",
                                        "gpu_supported", "gpu_note", "cores", "python_ok"})
            self.assertEqual(box["os"], platform_name)
            self.assertIsInstance(box["gpu_supported"], bool)
            self.assertIsInstance(box["gpus"], list)
            for key in ("ram_mib", "memavail_mib", "vram_mib", "cores"):
                self.assertIsInstance(box[key], int, "%s/%s" % (platform_name, key))
            self.assertGreater(box["cores"], 0)

    def test_disk_free_mib_is_mib_on_the_path_that_holds_it(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(detect.disk_free_mib(d), shutil.disk_usage(d).free // 1048576)

    def test_disk_free_mib_answers_for_a_directory_that_does_not_exist_yet(self):
        # Task 7 preflights --dir before creating it (the default is ~/Strata-data), so a path
        # that isn't there yet has to be answered, not raised.
        missing = os.path.join(tempfile.gettempdir(), "strata-no-such-dir-4242")
        self.assertEqual(detect.disk_free_mib(missing),
                         detect.disk_free_mib(tempfile.gettempdir()))
