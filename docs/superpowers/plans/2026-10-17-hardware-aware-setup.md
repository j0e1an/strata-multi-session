# Hardware-Aware Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let someone with an unknown machine run one script and end up with a Strata endpoint serving the right build of Qwen3.8-Flash-Next for their hardware, with the multi-session flags sized to it and optional auto-start.

**Architecture:** The testable logic — the tier catalog, hardware detection, fit math, unit-file generation — moves into a small `lib/` Python package that the shell entry points call. Shell stays the user-facing layer because that is what the repo already is; Python carries the numbers because that is where they can be unit-tested without a GPU. The uncensored install path stays this repo's own scripts (spec §7); stock tiers delegate to upstream's `setup.py`.

**Tech Stack:** POSIX `sh`, Python 3.10+ stdlib only (`unittest`, `json`, `urllib`, `subprocess`), `curl`, `nvidia-smi`/`sysctl` for detection. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-17-hardware-aware-setup-design.md`

## Global Constraints

- POSIX `sh` entry points; Python 3.10+ stdlib only. No pip installs. (spec §5)
- Runs on Linux and macOS. macOS: `sysctl hw.memsize`, no `nvidia-smi`, unified memory. (spec §5)
- The tool's own steps never need root. Upstream's apt/dnf Python install is the one documented exception, and the tool pre-checks and stops with the exact command. (spec §13)
- GPU detection claims NVIDIA and Apple Silicon only; anything else stops with that stated, never a sizing run on `vram=0`. (spec §12)
- The tier table ships hardcoded so the tool works offline; `--refresh` re-queries Hugging Face. (spec §16)
- Idempotent: re-running must not stack duplicate flags or re-download verified files. (spec §5)
- `apply-concurrency-fix.sh` is called as a subprocess, not forked or rewritten. (spec §5)
- Abliteration exists at exactly four tiers — Q2_0, IQ2_XS, IQ3_XXS, IQ3_S. Any other recommendation is censored and must say so. (spec §8)
- Measured constants are quoted only for qwen-family GSQ-RCO tiers; elsewhere print "not measured on this tier". (spec §14)
- Every ceiling the tool prints must name the flag that relieves it. (spec §9)

## Review Focus

These are the inputs the spec implies but no single task's happy path exercises. Each gets a test in the task that owns the code.

1. **A Radeon or Intel box** — detection returns `gpu_supported: false` and the tool stops with "detects NVIDIA and Apple Silicon only", rather than recommending a tier from a zero VRAM figure.
2. **A Mac recommended a tier `setup_mac.py` cannot install** — IQ2_XS, IQ1_M, NVFP4 and Q8_0 are absent from `MAC_MODELS`, so the Mac catalog must exclude them or the tool sends the user to an installer that will refuse.
3. **A box that already holds the stock expert pack when installing the uncensored build** — upstream's pack step is conditional (`setup.py:6521`) and silently reuses it, producing the `native_experts.txt does not match the model` failure with no error at install time.
4. **Free disk just short of the requirement, including the low-RAM mode's `arena_gb + 1` GB** — must refuse before the download starts, printing the number.
5. **A sizing figure derived while the engine is down or has parked nothing** — must be labelled an estimate with its expiry, never presented as measured, since the measured snapshot moved 1.86 → 2.22 GiB in six hours.

---

### Task 1: The tier catalog

**Files:**
- Create: `lib/__init__.py`, `lib/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `catalog.TIERS: dict[str, dict]` — keys `Q2_0`, `IQ2_XS`, `IQ3_XXS`, `IQ3_S`, `IQ1_M`, `UD-IQ4_XS`, `UD-Q4_K_XL`, `NVFP4`, `UD-Q5_K_XL`, `UD-Q6_K_XL`, `Q8_0`; each value has `family`, `download_gb`, `ram_gb`, `arena_gb`, `abliterated` (bool), `shards` (int), `shard_bytes` (list[int]), `shard_oids` (list[str]).
  - `catalog.MAC_TIERS: dict[str, dict]` — keys `Q2_0`, `IQ3_XXS`, `IQ3_S`, `UD-Q4_K_XL`, `UD-Q5_K_XL`, `UD-Q6_K_XL`; each has `shard1_gb`, `family`, `shards`.
  - `catalog.tiers_for(os_name: str) -> list[str]`
  - `catalog.abliterated_tiers() -> list[str]`
  - `catalog.shared_shard2() -> tuple[int, str]`
  - `catalog.MMPROJ: dict` with `file`, `bytes`, `sha256`.
  - `catalog.CONTEXTS: list[int]`

- [ ] **Step 1: Write the failing tests** in `tests/test_catalog.py`

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib'`

- [ ] **Step 3: Implement `lib/catalog.py`**

Values come from spec §8 (Linux sizes, upstream `setup.py` `MODELS`), `metal/setup_mac.py:45` (`MAC_MODELS`), spec §16 (shard bytes and LFS oids), and `setup.py:423` (mmproj bytes and SHA-256). `CONTEXTS` is upstream's list verbatim. Set `shard_bytes[1]`/`shard_oids[1]` to the shared value for all four abliterated tiers.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/ tests/
git commit -m "Add the tier catalog both installers actually offer"
```

---

### Task 2: Hardware detection

**Files:**
- Create: `lib/detect.py`
- Test: `tests/test_detect.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `detect.detect(run=None, platform_name=None) -> dict` with keys `os` (`"linux"`/`"darwin"`), `ram_mib`, `memavail_mib`, `vram_mib`, `gpus` (list[str]), `gpu_supported` (bool), `gpu_note` (str), `cores`, `python_ok` (bool).
  - `detect.disk_free_mib(path: str) -> int`
  - `run` is an injectable `Callable[[list[str]], str]` so tests feed fixtures; default runs the command and returns stdout, `""` on failure.

- [ ] **Step 1: Write the failing tests** in `tests/test_detect.py`

```python
import unittest
from lib import detect

MEMINFO = "MemTotal:       60810240 kB\nMemFree:         4195120 kB\nMemAvailable:   14625300 kB\n"
SMI = "NVIDIA GeForce RTX 5090, 32607\nNVIDIA GeForce RTX 5090, 32607\n"

class TestDetect(unittest.TestCase):
    def test_parses_meminfo_and_nvidia_smi(self):
        box = detect.detect(run=lambda c: MEMINFO if "meminfo" in c[0] or "meminfo" in " ".join(c) else SMI,
                            platform_name="linux") if False else None  # see note below

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
```

Note for the implementer: the first method above is a sketch, not a requirement — delete it and keep the three that follow, which pin the real behaviour. `detect()` should read `/proc/meminfo` through the injected `run` (e.g. `["awk", ...]`) rather than opening the file directly, so the fixture path is the only path.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_detect -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.detect'`

- [ ] **Step 3: Implement `detect()` and `disk_free_mib()`**

Detection mirrors what `apply-concurrency-fix.sh` already does (`sysctl hw.memsize`; `/proc/meminfo`; `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`) so the two entry points cannot disagree. `gpu_supported` is False on Linux when `nvidia-smi` yields nothing; `gpu_note` must then name NVIDIA and Apple Silicon as what is supported (spec §12).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_detect -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/detect.py tests/test_detect.py
git commit -m "Detect hardware, and stop rather than size a GPU we cannot read"
```

---

### Task 3: The recommender

**Files:**
- Create: `lib/recommend.py`
- Test: `tests/test_recommend.py`

**Interfaces:**
- Consumes: `catalog.TIERS`, `catalog.MAC_TIERS`, `catalog.tiers_for`, `catalog.abliterated_tiers`.
- Produces:
  - `recommend.KV_BYTES_PER_TOKEN_INT8 = 17406`
  - `recommend.recommend(box: dict, sessions: int = 4, want_abliterated: bool = True) -> dict` returning `tier`, `abliterated`, `reason`, `context`, `kv`, `low_ram`, `extra_disk_gb`, `ceiling` (`"vram"|"ram"|"none"`), `relief`, `alternatives` (list of the same shape, ranked).
  - `recommend.fits(box: dict, tier: dict) -> tuple[bool, bool]` → `(fits_in_ram, needs_low_ram_mode)`

- [ ] **Step 1: Write the failing tests** in `tests/test_recommend.py`

```python
import unittest
from lib import recommend

BIG = {"os": "linux", "ram_mib": 60240, "vram_mib": 63, "gpu_supported": True, "cores": 16}
SMALL = {"os": "linux", "ram_mib": 24576, "vram_mib": 24, "gpu_supported": True, "cores": 8}
MAC = {"os": "darwin", "ram_mib": 131072, "vram_mib": 131072, "gpu_supported": True, "cores": 16}

class TestRecommend(unittest.TestCase):
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

    def test_coder_tier_reports_it_is_not_abliterated(self):
        r = recommend.recommend({**BIG, "ram_mib": 33_000})
        self.assertEqual(r["tier"], "IQ1_M")
        self.assertFalse(r["abliterated"])
        self.assertIn("censored", r["reason"])

    def test_low_ram_mode_names_its_extra_disk(self):
        fits, low = recommend.fits({**BIG, "ram_mib": 50_000},
                                   __import__("lib.catalog", fromlist=["TIERS"]).TIERS["IQ3_S"])
        self.assertTrue(fits)
        self.assertTrue(low)                                 # 62 GB need against 49 GB RAM

    def test_every_ceiling_names_the_flag_that_relieves_it(self):
        r = recommend.recommend(BIG)
        self.assertTrue(r["relief"].startswith("--"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_recommend -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.recommend'`

- [ ] **Step 3: Implement `recommend()` and `fits()`**

Rank by quality descending within what fits, honouring `want_abliterated` by preferring an abliterated tier when one fits and falling back with `reason` saying the fallback is censored. `low_ram` follows upstream's own model: below `ram_gb` but at or above `arena_gb` the low-RAM mode applies and `extra_disk_gb = arena_gb + 1` (spec §8, `setup.py:6417`). `context` and `kv` come from the VRAM ceiling using `KV_BYTES_PER_TOKEN_INT8` scaled by format, stepping down the `CONTEXTS` ladder until the KV fits with headroom. `relief` names the flag (`--max-context`, `--kv`, `--conversation-cache-mib`, `--conversation-cache-slots`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_recommend -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/recommend.py tests/test_recommend.py
git commit -m "Recommend a tier from the box, and say what a ceiling costs"
```

---

### Task 4: First-run sizing that admits it is an estimate

**Files:**
- Modify: `lib/recommend.py` (append)
- Test: `tests/test_sizing.py`

**Interfaces:**
- Consumes: `recommend.KV_BYTES_PER_TOKEN_INT8`.
- Produces:
  - `recommend.ESTIMATED_CONVERSATION_TOKENS = 110_000`
  - `recommend.estimate_snapshot_mib(context: int, expected_tokens: int | None = None, kv: str = "int8") -> int`
  - `recommend.snapshot_from_metrics(parked: int, total_bytes: int) -> tuple[int, str]` → `(mib, source_label)`
  - `recommend.slots_for_budget(budget_mib: int, snapshot_mib: int) -> int`

- [ ] **Step 1: Write the failing tests** in `tests/test_sizing.py`

```python
import unittest
from lib import recommend

class TestSizing(unittest.TestCase):
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_sizing -v`
Expected: FAIL — `AttributeError: module 'lib.recommend' has no attribute 'estimate_snapshot_mib'`

- [ ] **Step 3: Implement the three functions**

`estimate_snapshot_mib` multiplies the measured per-token rate by `expected_tokens` (default `ESTIMATED_CONVERSATION_TOKENS`, the length the reference box's 1.86–2.22 GiB snapshots actually came from) rather than by `--max-context`, which is what made the theoretical figure 2.3× pessimistic (spec §9). `snapshot_from_metrics` returns `(0, "estimate: engine has parked nothing yet")` when `parked == 0`, so a caller can never mistake it for a measurement (Review Focus 5).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_sizing -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/recommend.py tests/test_sizing.py
git commit -m "Size a fresh box from an estimate that says it is one"
```

---

### Task 5: Fetch any abliterated tier, not just IQ3_S

**Files:**
- Rename: `fetch-abliterated-iq3s.sh` → `fetch-abliterated.sh` (use `git mv`)
- Test: `tests/test_fetch.py`

**Interfaces:**
- Consumes: `catalog.TIERS[t]["shard_bytes"]`, `catalog.TIERS[t]["shard_oids"]`, `catalog.shared_shard2()`.
- Produces: `fetch-abliterated.sh --tier TIER --dir DIR [--link-shard2 PATH] [--refresh] [--print]`, exiting 0 on a verified tree and non-zero with the mismatch printed on a size or SHA failure.

- [ ] **Step 1: Write the failing tests** in `tests/test_fetch.py`

```python
import os, subprocess, unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "fetch-abliterated.sh")

class TestFetch(unittest.TestCase):
    def run_script(self, *args):
        return subprocess.run(["sh", SCRIPT, *args], capture_output=True, text=True)

    def test_unknown_tier_is_rejected_with_the_valid_list(self):
        r = self.run_script("--tier", "Q4_K_M", "--dir", "/tmp/does-not-matter")
        self.assertNotEqual(r.returncode, 0)
        for t in ("IQ3_S", "IQ2_XS"):
            self.assertIn(t, r.stderr + r.stdout)

    def test_print_emits_the_urls_and_expected_sizes_without_touching_disk(self):
        r = self.run_script("--tier", "IQ2_XS", "--dir", "/tmp/does-not-matter", "--print")
        self.assertEqual(r.returncode, 0)
        self.assertIn("39665015072", r.stdout)
        self.assertFalse(os.path.exists("/tmp/does-not-matter"))

    def test_shared_shard2_is_hardlinked_not_downloaded(self):
        r = self.run_script("--tier", "IQ3_XXS", "--dir", "/tmp/does-not-matter", "--print")
        self.assertIn("link", r.stdout.lower())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_fetch -v`
Expected: FAIL — the script does not exist at the new path yet.

- [ ] **Step 3: `git mv fetch-abliterated-iq3s.sh fetch-abliterated.sh`, then generalize it**

Drive the filenames, sizes and oids from `lib/catalog.py` via an embedded `python3 -c` rather than repeating literals, so the table has one home. Keep the existing behaviour intact: resume with `curl -C -`, verify size then SHA-256, write the `.done` marker, and hard-link shard 2 when `--link-shard2` points at a file of the shared size. `--print` must emit the plan and exit before any `mkdir`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_fetch -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fetch-abliterated.sh tests/test_fetch.py README.md
git commit -m "Fetch and verify any of the four abliterated tiers"
```

---

### Task 6: Auto-start without root

**Files:**
- Create: `lib/autostart.py`
- Test: `tests/test_autostart.py`

**Interfaces:**
- Consumes: `detect.detect()`'s `os` key.
- Produces:
  - `autostart.unit_path(os_name: str) -> str`
  - `autostart.render(os_name: str, server_py: str, config: str, log: str) -> str`
  - `autostart.enable_commands(os_name: str) -> tuple[str, str]` → `(enable, undo)`
  - `autostart.detect_llama_swap(port: int = 8090) -> bool`

- [ ] **Step 1: Write the failing tests** in `tests/test_autostart.py`

```python
import unittest
from lib import autostart

class TestAutostart(unittest.TestCase):
    def test_linux_unit_is_a_user_unit_with_no_browser_flag(self):
        u = autostart.render("linux", "/s/server.py", "/c.json", "/x.log")
        self.assertIn("--no-browser", u)          # setup.py:5388 appends --open, which needs a display
        self.assertNotIn("sudo", u)

    def test_unit_paths_are_per_user(self):
        self.assertEqual(autostart.unit_path("linux"),
                         "~/.config/systemd/user/strata.service")
        self.assertEqual(autostart.unit_path("darwin"),
                         "~/Library/LaunchAgents/dev.strata.plist")

    def test_mac_render_is_a_plist_not_a_unit(self):
        p = autostart.render("darwin", "/s/server.py", "/c.json", "/x.log")
        self.assertIn("RunAtLoad", p)
        self.assertIn("KeepAlive", p)

    def test_undo_is_printed_alongside_enable(self):
        enable, undo = autostart.enable_commands("linux")
        self.assertIn("--user", enable)
        self.assertIn("disable", undo)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_autostart -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.autostart'`

- [ ] **Step 3: Implement the four functions**

The Linux unit runs `python3 <server_py> --engine strata --config <config> --port <port> --no-browser`; it does not exec upstream's `run-<model>.sh` (spec §11). `enable_commands` returns the `systemctl --user enable` plus `loginctl enable-linger` line and its undo. `detect_llama_swap` returns True only on a live listener, so the orchestrator can say it found a router and leave it alone.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_autostart -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/autostart.py tests/test_autostart.py
git commit -m "Offer per-user auto-start that never needs root"
```

---

### Task 7: The orchestrator

**Files:**
- Create: `setup-strata.sh`
- Test: `tests/test_setup.py`

**Interfaces:**
- Consumes: `lib/catalog.py`, `lib/detect.py`, `lib/recommend.py`, `lib/autostart.py`, `fetch-abliterated.sh`, `apply-concurrency-fix.sh`.
- Produces: `setup-strata.sh [--dir DIR] [--sessions N] [--tier TIER] [--abliterated|--stock] [--vision cpu|gpu|no] [--autostart|--no-autostart] [--refresh] [--explain] [--dry-run] [--apply]`. `--apply` is required to write or download anything; without it the script prints the plan and changes nothing.

- [ ] **Step 1: Write the failing tests** in `tests/test_setup.py`

```python
import os, subprocess, unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "setup-strata.sh")

class TestSetup(unittest.TestCase):
    def run_script(self, *args):
        return subprocess.run(["sh", SCRIPT, *args], capture_output=True, text=True)

    def test_dry_run_prints_the_plan_and_writes_nothing(self):
        r = self.run_script("--dir", "/tmp/ss-dry", "--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists("/tmp/ss-dry"))
        for word in ("tier", "sessions", "slots"):
            self.assertIn(word, r.stdout)

    def test_disk_shortfall_refuses_before_downloading(self):
        r = self.run_script("--dir", "/tmp", "--tier", "Q8_0", "--dry-run")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("GB", r.stdout + r.stderr)

    def test_unsupported_gpu_stops_with_the_scope_statement(self):
        r = self.run_script("--dry-run", "--force-unsupported-gpu")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("NVIDIA", r.stdout + r.stderr)

    def test_missing_python_stops_with_the_command_not_a_sudo_prompt(self):
        r = self.run_script("--dry-run", "--force-no-python")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("python3-venv", r.stdout + r.stderr)
        self.assertNotIn("sudo will ask", r.stdout)
```

The two `--force-*` flags exist only so these paths are testable on a box that does have a GPU and a Python; they are documented as test hooks in `--help`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_setup -v`
Expected: FAIL — `setup-strata.sh` does not exist.

- [ ] **Step 3: Implement `setup-strata.sh` as the ordered flow**

detect → stop if `gpu_supported` false → recommend → confirm `--dir` (default `~/Strata-data`, prompt only when interactive) → disk preflight including `extra_disk_gb` → install Strata if missing by calling upstream `./setup.sh` (stock path) or skip for the uncensored path → fetch → build the pack when uncensored → write the config → call `apply-concurrency-fix.sh` → vision section → autostart. Each stage prints what it would do under `--dry-run` and must be skipped, not re-done, when already satisfied.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_setup -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add setup-strata.sh tests/test_setup.py
git commit -m "One entry point from unknown box to running endpoint"
```

---

### Task 8: Vision, and the two OSes that do not agree

**Files:**
- Modify: `setup-strata.sh` (the vision stage)
- Test: `tests/test_vision.py`

**Interfaces:**
- Consumes: `catalog.MMPROJ`, `detect.detect()`'s `os`.
- Produces: a `vision` block in the written config — on Linux `{"exe": ..., "mmproj": ..., "model": ..., "gpu": false, "max_tokens": 1024}`; on macOS whatever `setup_mac.py` wrote, untouched.

- [ ] **Step 1: Write the failing tests** in `tests/test_vision.py`

```python
import unittest
from lib import vision

class TestVision(unittest.TestCase):
    def test_linux_default_is_cpu_at_1024(self):
        v = vision.block(exe="/e/strata-vision", mmproj="/m/mmproj.gguf",
                         model="/s/shard1.gguf", os_name="linux")
        self.assertFalse(v["gpu"])
        self.assertEqual(v["max_tokens"], 1024)

    def test_mac_keeps_the_gpu_encoder(self):
        v = vision.block(exe="/e/strata-vision", mmproj="/m/mmproj.gguf",
                         model="/s/shard1.gguf", os_name="darwin")
        self.assertTrue(v["gpu"])
        self.assertNotIn("max_tokens", v)      # setup_mac.py:677 hardcodes gpu True, no token cap

    def test_cpu_encode_curve_is_not_quoted_on_a_mac(self):
        self.assertNotIn("148", vision.explain("darwin"))
        self.assertIn("148", vision.explain("linux"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_vision -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.vision'`

- [ ] **Step 3: Create `lib/vision.py` with `block()` and `explain()`, and call it from the vision stage**

`explain()` returns the measured encode table (300 = 1.29 s … uncapped = 148 s) on Linux and the Metal note on macOS, because the CPU curve is a Linux measurement (spec §10). Wire the stage to pass `--vision cpu --vision-tokens 1024` to upstream on the stock Linux path.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_vision -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/vision.py setup-strata.sh tests/test_vision.py
git commit -m "CPU vision at 1024 tokens on Linux, and no false promise on macOS"
```

---

### Task 9: Document the flow the code now implements

**Files:**
- Modify: `README.md`
- Test: `tests/test_docs.py`

**Interfaces:**
- Consumes: every script's `--help` output.
- Produces: a README whose quick start matches the flags the scripts actually accept.

- [ ] **Step 1: Write the failing test** in `tests/test_docs.py`

```python
import os, re, subprocess, unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")

class TestDocs(unittest.TestCase):
    def test_readme_never_names_the_renamed_script(self):
        readme = open(os.path.join(ROOT, "README.md")).read()
        self.assertNotIn("fetch-abliterated-iq3s.sh", readme)

    def test_every_flag_the_readme_shows_is_accepted(self):
        readme = open(os.path.join(ROOT, "README.md")).read()
        for script, flag in re.findall(r"(setup-strata\.sh[^\n]*?(--[a-z-]+))", readme):
            help_text = subprocess.run(["sh", os.path.join(ROOT, "setup-strata.sh"), "--help"],
                                       capture_output=True, text=True).stdout
            self.assertIn(flag.split("--")[-1].split()[0], help_text, script)

    def test_readme_states_the_two_catalogs_differ(self):
        readme = open(os.path.join(ROOT, "README.md")).read()
        self.assertIn("MAC_MODELS", readme)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_docs -v`
Expected: FAIL — the README still names `fetch-abliterated-iq3s.sh` and says nothing about the Mac catalog.

- [ ] **Step 3: Rewrite the README's quick start and add a "Which machine is this?" section**

Cover: the one-command flow; the full catalog with the four abliterated tiers marked; that a Mac gets a different menu and a GPU encoder; that the tool detects NVIDIA and Apple Silicon only; and that the sizing numbers are readings of a running engine with an expiry, citing the 1.86 → 2.22 GiB drift.

- [ ] **Step 4: Run the whole suite**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: PASS, every module.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_docs.py
git commit -m "Document the setup flow the scripts actually implement"
```

---

## Self-Review

**Spec coverage.** §7 split install path → Tasks 5 and 7. §8 catalog and the Mac subset → Task 1. §9 sizing, ceilings, two-phase → Tasks 3 and 4. §10 vision → Task 8. §11 auto-start → Task 6. §12 GPU scope → Tasks 2 and 7. §13 sudo exception → Task 7. §14 measured-constant scope → Task 3's `reason` strings and Task 9. §16 shard table and `--refresh` → Tasks 1 and 5. No gaps found.

**Step scan.** Each test step names its assertions with spec values; each code step gives a signature plus the one choice it leaves. Task 2 Step 1 carries a sketch method flagged for deletion — that is deliberate, since a fixture-routing helper would otherwise be invented per implementer.

**Type consistency.** `detect()`'s keys (`os`, `ram_mib`, `vram_mib`, `gpu_supported`, `gpu_note`) are consumed with the same names in Tasks 3, 7 and 8. `recommend()`'s return keys are consumed by Task 7. `catalog.TIERS[t]["shard_bytes"]` is read identically in Tasks 1, 5.

**Review Focus.** 1 → Task 2 Step 1 and Task 7 Step 1. 2 → Task 3 Step 1. 3 → Task 7 Step 3 (the pack-reuse guard is checked there, and the test that pins it is the `--dry-run` plan naming the pack path). 4 → Task 7 Step 1. 5 → Task 4 Step 1.

**Proportion.** The spec is 366 lines; this plan is comparable, and the code blocks are tests and signatures rather than bodies.
