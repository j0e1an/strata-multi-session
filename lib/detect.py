"""What the box actually is: RAM, VRAM, GPU, cores, and a Python that can make a venv.

Every figure here is a reading of the running machine, taken with the same commands
``apply-concurrency-fix.sh`` already uses — ``sysctl -n hw.memsize``, the MemTotal and
MemAvailable lines of ``/proc/meminfo``, and ``nvidia-smi --query-gpu=name,memory.total
--format=csv,noheader,nounits`` — so the two entry points cannot tell a user two different
stories about the same hardware.

Two decisions are why this is a module with an injectable runner rather than another shell
snippet. Both are stop-paths that cannot be produced on a box with an NVIDIA card and a
working Python — the author's — so without the seam they ship untested:

* **GPU scope (spec §12): NVIDIA and Apple Silicon, nothing else.** On Linux, no
  ``nvidia-smi`` output means ``gpu_supported: False`` and a ``gpu_note`` that says what this
  tool *does* detect, so the caller stops there instead of recommending a tier from a
  ``vram_mib`` of zero. Strata itself supports AMD (``--backend hip``) and Intel
  (``sycl/setup_intel.py``); this tool cannot size them, and the note says which of the two
  facts it is asserting.
* **The Python pre-flight (spec §13).** The tool needs no root, but upstream's installer does
  the moment the box has no Python 3.10+ that can make a venv *with* pip. The test is
  upstream's own (``setup.sh:9``): import ``venv`` **and** ``ensurepip``. Debian and Ubuntu
  ship ``venv`` without ``ensurepip`` — that is the separate ``python3-venv`` package — and a
  venv built without it has no pip, so importing ``venv`` alone waves through a box that fails
  two steps later with a sudo prompt.

Units, because the key names are fixed by the plan and are not uniform: ``ram_mib`` and
``memavail_mib`` are MiB, while ``vram_mib`` is whole GiB — it is ``vram_gib`` in
``apply-concurrency-fix.sh``, and Tasks 3 and 7 compare it against the catalog's ``*_gb``
columns. See ``_gpus_from_smi()`` for the one place the two entry points can differ by a GiB.

``run`` is the seam: a callable taking an argv list and returning stdout, ``""`` on failure.
Nothing else in this module touches the filesystem or a subprocess.
"""

import os
import shutil
import subprocess
import sys
from typing import Callable

# Upstream's own test, setup.sh:9: `import sys, venv, ensurepip` behind a 3.10 floor. setup.sh
# reads the exit code; `run` carries stdout only, so the verdict has to be printed — a box that
# fails either half prints nothing, and "" is what `run` returns for any failure anyway.
PY_PROBE = "import sys, venv, ensurepip; print('ok' if sys.version_info >= (3, 10) else '')"
PY_CMD = ["python3", "-c", PY_PROBE]

# The same two fields apply-concurrency-fix.sh reads, and the same kB -> MiB conversion (//1024).
# awk narrows the file to the lines wanted; the parser still handles the raw file, so a fixture
# can be a copy of /proc/meminfo.
MEMINFO_CMD = ["awk", "/^Mem(Total|Available):/ { print $1, $2 }", "/proc/meminfo"]

# Same query and CSV shape as apply-concurrency-fix.sh. `nounits` means memory.total arrives
# as a bare number in MiB.
NVIDIA_CMD = ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]
MEMSIZE_CMD = ["sysctl", "-n", "hw.memsize"]

# What a stop has to say: the scope statement of spec §12, phrased so it is a limit of this
# tool and not a claim that Strata lacks the backend.
NO_NVIDIA_NOTE = ("no NVIDIA GPU reported — this tool detects NVIDIA and Apple Silicon only "
                  "(Strata supports AMD and Intel; this tool cannot size them)")
NO_MEMSIZE_NOTE = ("could not read hw.memsize — this tool detects NVIDIA and Apple Silicon "
                   "only, and cannot size a Mac it cannot read")
APPLE_NOTE = "Apple Silicon unified memory (no discrete VRAM)"

MIB = 1_048_576


def default_run(cmd: list[str]) -> str:
    """Run argv and return its stdout; ``""`` on any failure, including a missing binary.

    The Python twin of ``sh()`` in apply-concurrency-fix.sh, for the same reason: a box with no
    ``nvidia-smi`` is the Radeon path, not a traceback, and the caller decides what a blank
    means.
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""


def _mib_from_meminfo(text: str) -> tuple[int, int]:
    """``(ram_mib, memavail_mib)`` from /proc/meminfo text; 0 for a field the text lacks."""
    wanted = {"MemTotal": 0, "MemAvailable": 0}
    for line in text.splitlines():
        parts = line.split()
        key = parts[0].rstrip(":") if parts else ""
        if key in wanted and len(parts) > 1 and parts[1].isdigit():
            wanted[key] = int(parts[1]) // 1024                      # kB -> MiB
    return wanted["MemTotal"], wanted["MemAvailable"]


def _gpus_from_smi(text: str) -> tuple[list[str], int]:
    """``([gpu names], total VRAM in whole GiB)`` from nvidia-smi's CSV; ``([], 0)`` on nothing.

    The MiB figures are summed *before* the single GiB conversion. apply-concurrency-fix.sh
    converts each card first, which truncates every one of them (32607 MiB -> 31 GiB), so a
    2×5090 box reads 62 GiB there and 63 here. The summed figure is the honest one and the one
    the plan's fixtures and Task 3's fit math use; the shell script is the one that should
    change, not this.
    """
    names, mib = [], 0
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[1].isdigit():
            names.append(parts[0])
            mib += int(parts[1])
    return names, mib // 1024


def detect(run: Callable[[list[str]], str] | None = None,
           platform_name: str | None = None) -> dict:
    """Describe this machine, the way the tier recommender needs it described.

    ``run`` defaults to :func:`default_run`; pass a fixture-answering callable to describe a
    box that isn't this one. ``platform_name`` defaults to ``sys.platform`` and, exactly as in
    apply-concurrency-fix.sh, anything that isn't ``darwin`` is treated as Linux — which is
    what this repo supports, so a Windows box goes down the Linux path rather than crashing.

    ``gpu_supported`` False is a stop, not a zero: the caller must not size a tier from the
    ``vram_mib`` it gets back, and ``gpu_note`` says what the tool does detect.
    """
    run = run or default_run
    box = {
        "os": "darwin" if (platform_name or sys.platform) == "darwin" else "linux",
        "ram_mib": 0,
        "memavail_mib": 0,
        "vram_mib": 0,
        "gpus": [],
        "gpu_supported": False,
        "gpu_note": "",
        "cores": os.cpu_count() or 0,
        # spec §13: the one step that can need root, pre-checked so the caller can print the
        # package command instead of inheriting a sudo prompt mid-install.
        "python_ok": bool(run(PY_CMD).strip()),
    }

    if box["os"] == "darwin":
        # Apple Silicon unifies system and GPU memory, so hw.memsize (bytes) is both figures and
        # there is no nvidia-smi to ask. memavail_mib stays 0: the MemAvailable misreading that
        # --memory-limit-mib exists for is a Linux one, and apply-concurrency-fix.sh prints it
        # only off the Linux branch for the same reason. "Apple Silicon" is named, not probed --
        # that script labels every darwin box the same way, and upstream's own Mac installer is
        # what refuses an Intel CPU (metal/setup_mac.py:155). A Mac whose sysctl fails is a stop,
        # not a 0 GiB box.
        memsize = run(MEMSIZE_CMD).strip()
        box["ram_mib"] = int(memsize) // MIB if memsize.isdigit() else 0
        box["vram_mib"] = box["ram_mib"]
        box["gpu_supported"] = box["ram_mib"] > 0
        box["gpus"] = ["Apple Silicon"] if box["gpu_supported"] else []
        box["gpu_note"] = APPLE_NOTE if box["gpu_supported"] else NO_MEMSIZE_NOTE
    else:
        box["ram_mib"], box["memavail_mib"] = _mib_from_meminfo(run(MEMINFO_CMD))
        names, vram = _gpus_from_smi(run(NVIDIA_CMD))
        box["gpus"], box["vram_mib"] = names, vram
        box["gpu_supported"] = bool(names)
        box["gpu_note"] = " + ".join(names) if names else NO_NVIDIA_NOTE

    return box


def disk_free_mib(path: str) -> int:
    """MiB free on the filesystem that would hold ``path``.

    A path that does not exist yet is answered from the nearest existing parent: the disk
    preflight runs before anything is created (the default install dir is ``~/Strata-data``),
    and the space a not-yet-created directory will use is its parent's.
    """
    asked = os.path.abspath(path)
    while not os.path.exists(asked):
        parent = os.path.dirname(asked)
        if parent == asked:                           # reached the root; nothing to ask
            return 0
        asked = parent
    return shutil.disk_usage(asked).free // MIB
