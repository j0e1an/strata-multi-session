"""Which tier this box can run, and what each ceiling costs to relieve.

This is the answer to the question the whole tool exists to answer (spec §1): *can this machine
run this model, and which build of it?* It takes a box from :func:`lib.detect.detect` and returns
a recommendation with the arithmetic in the ``reason`` the user reads, plus the same shape ranked
behind it as ``alternatives``. Nothing is fetched and nothing is run: every figure is already in
the box dict and in :mod:`lib.catalog`, so the tool can answer with no network (spec §16).

Units, and why the conversion is not ``/ 1024``
    The catalog's ``*_gb`` columns are **decimal** GB: ``download_gb``/``arena_gb`` are byte
    counts that upstream compares against ``free_gb()`` (``shutil.disk_usage().free / 1e9``,
    setup.py:1596-1598), and upstream itself converts them to GiB before comparing them with its
    GiB RAM figure (``MODELS[model]["arena_gb"] / 1.073741824``, setup.py:1875 and :1892). A
    ``*_mib`` reading therefore becomes comparable by ``* 1048576 / 1e9``, not by ``/ 1024`` —
    ``_gb()`` is the one place that step happens. The reference box decides the argument: read on
    this box on 2026-10-17 its ``MemTotal`` is 61935996 kB = 60484 MiB = 63.42 GB, and it runs
    IQ3_S, whose ``ram_gb`` is 62 (``reference/strata-iq3_s-abliterated.json``, with
    ``--resident-experts``). Read as 59.07 GiB it would fail IQ3_S's floor, and the tool would
    refuse the one machine this repo has measured running that tier.

Two predicates, deliberately not the same strictness
    :func:`fits` is the permissive question — *could this tier run here at all*, including with
    upstream's low-RAM mode mapping the experts from disk. :func:`recommend` answers the
    conservative question — *what should this box install* — and holds the tier's published RAM
    floor, ``ram >= ram_gb``, which is exactly upstream's own pre-selection rule (setup.py:2247,
    ``fitting = [m for m in names if ram >= MODELS[m]["ram_gb"]]``). A 24 GB box therefore gets
    "no tier" from :func:`recommend` even though :func:`fits` can say IQ1_M could be made to run:
    the low-RAM mode is a degraded mode, and this tool does not reach a tier by degrading into it
    silently.

Stops, not zeros (spec §12)
    ``gpu_supported`` False, ``ram_mib`` 0 or ``vram_mib`` 0 returns no tier at all. A Linux box
    with no ``awk`` reports ``ram_mib: 0`` beside ``gpu_supported: True``, and a card can report 0
    MiB; sizing either from that zero is the guess §12 exists to prevent, and every tier's floor
    is above a zero anyway.

Every number here names its source:
    17,406 bytes/token at ``--kv int8``   measured (spec §9); the per-format scale is the engine's
                                          own per-cell bytes — 1056 for int8, 816 for k8v4, 576
                                          for q4_0 (setup.py:198 and its ``--kv`` help), which is
                                          the check spec §9 names
    ``--kv`` choices                      setup.py:5722
    ~5 GB of VRAM for the dense weights,
    buffers and a short context           setup.py:4017-4023 (``low_ram_gpu_gb``)
    the expert cache keeps half the arena  setup.py:5155 (``PARALLEL_HELD``), the floor upstream
                                          applies before it lets anything else take VRAM
    10 GB beside the experts              setup.py:3999 (``LOW_RAM_HEADROOM_GB``), the test at
                                          setup.py:4011-4014 that decides the low-RAM mode applies
    arena + 1 GB of extra disk            setup.py:6418-6420 (the disk preflight's ``need``)
    24 GB beside the model                setup.py:5210 (``AGENT_CACHE_FREE_GB``, bench #882 #440),
                                          upstream's own line for "this box has room for a
                                          conversation cache"
    262144                                the trained window (spec §8/§14); 204800 is a menu step
                                          upstream never recommends (#608, setup.py:4237) and
                                          393216/524288 sit above the trained window, so the
                                          ladder stops at 262144
    the four abliterated tiers            spec §8; ``catalog.abliterated_tiers()``

``sessions`` sizes the conversation cache, which is the RAM ceiling's subject; the per-session
snapshot cost is measured against a running engine (``estimate_snapshot_mib`` /
``slots_for_budget``, added next), so here it appears in the ceiling's text rather than in the
tier choice — RAM caps how many sessions stay warm, it does not change which tier is resident.

``box["has_shared_shard2"]`` (optional, falsy by default) says the target directory already holds
the 28.8 GB shard 2 that every abliterated tier shares (``catalog.shared_shard2()``); the disk
figure then counts it once, not twice (spec §16). The relief flags named are the engine arguments
the config carries; on macOS the installer's own menu is narrower than the config's (spec §8).
"""

from lib import catalog

# Measured, not modelled: snapshot_bytes 3,892,476,616 / 223,627 tokens at --kv int8 and 262144
# on the reference box (spec §9). Everything downstream scales from this one figure.
KV_BYTES_PER_TOKEN_INT8 = 17_406

# The engine's per-cell bytes, which is how a format other than int8 scales the measured rate
# (setup.py:198 for q4_0 and k8v4; int8's 1056 is the engine's own help). Order is upstream's
# --kv list, best quality first (setup.py:5722).
KV_CELL_BYTES = {"int8": 1056, "k8v4": 816, "q4_0": 576}
KV_FORMATS = ("int8", "k8v4", "q4_0")

# The trained window. Above it the context needs rope scaling and no KV figure here was measured
# there, so it is the top of the ladder this tool recommends rather than a step it walks.
TRAINED_CONTEXT = 262_144

# Upstream's ladder (setup.py:381, verbatim in catalog.CONTEXTS) cut to what this tool will
# recommend: nothing above the trained window, and 204800 dropped because #608 says the 200K menu
# step is never the recommendation.
CONTEXT_LADDER = tuple(c for c in catalog.CONTEXTS if c <= TRAINED_CONTEXT and c != 204_800)

# setup.py:4017-4023: what a card holds before any KV or expert cache is counted.
DENSE_WEIGHTS_GB = 5.0

# setup.py:5155 (PARALLEL_HELD): the VRAM the KV cache may take is whatever is left once the
# expert cache still holds this share of the model's experts. Below that the engine stops with
# "no VRAM is left for the expert cache" (setup.py:5097).
EXPERT_CACHE_HELD = 0.5

# setup.py:3999 / :4011-4014: the RAM the experts need beside them, and the test that puts the
# installer into the low-RAM mode when it is not there.
LOW_RAM_HEADROOM_GB = 10

# setup.py:5210, bench #882 #440: RAM left beside the model for the conversation cache. Under
# this there is no cache to size, which is the RAM ceiling.
AGENT_CACHE_FREE_GB = 24

# The family the 17,406 B/token rate and the parked-snapshot range were measured on (spec §14).
# Anywhere else the figure has to be labelled as carried over, and the label goes in the text the
# user sees, not only in this file.
MEASURED_FAMILY = ("qwen",)
NOT_MEASURED = (" (that rate is measured on qwen-family GSQ-RCO IQ3_S at 262144, not measured on "
                "this tier)")

MIB = 1_048_576
GB = 1e9


def _gb(mib: int) -> float:
    """A ``*_mib`` reading in the same decimal GB the catalog's ``*_gb`` columns are in.

    See the module docstring: the step is ``* 1048576 / 1e9`` because the columns are byte counts,
    and ``/ 1024`` (GiB) would refuse the reference box the tier it runs.
    """
    return mib * MIB / GB


def _kv_gb(context: int, kv: str) -> float:
    """GB of VRAM one conversation's KV cache takes at ``context`` tokens in format ``kv``."""
    scale = KV_CELL_BYTES[kv] / KV_CELL_BYTES["int8"]
    return context * KV_BYTES_PER_TOKEN_INT8 * scale / GB


def _kv_room(vram_gb: float, tier: dict) -> float:
    """GB of VRAM the KV cache may take: the card less the dense weights, less the experts the
    cache still has to hold. Negative when the card cannot hold both, which is a real answer."""
    return max(0.0, vram_gb - DENSE_WEIGHTS_GB) - EXPERT_CACHE_HELD * tier["arena_gb"]


def _kv_plan(vram_gb: float, tier: dict) -> tuple[int, str, bool]:
    """``(context, kv, fits_in_room)`` — the longest context whose KV fits this VRAM, and the
    format that buys it.

    Walks ``CONTEXT_LADDER`` down from the trained window at the best format, then the next format
    down — the order a user would trade them in, and the two flags ``relief`` names. Where even
    the shortest context at the cheapest format does not fit, the answer is that floor with
    ``fits_in_room`` False: upstream runs such a card slowly rather than refusing it (its ~8 GB
    card note, setup.py:5139-5146), but it says the card is the problem first.
    """
    room = _kv_room(vram_gb, tier)
    for kv in KV_FORMATS:
        for context in reversed(CONTEXT_LADDER):
            if _kv_gb(context, kv) <= room:
                return context, kv, True
    return CONTEXT_LADDER[0], KV_FORMATS[-1], False


def _low_ram_applies(ram_gb: float, tier: dict) -> bool:
    """Would the installer put this tier in the low-RAM mode on ``ram_gb`` GB of RAM?

    Upstream's own test (setup.py:4011-4014): the experts do not fit with the usual
    ``LOW_RAM_HEADROOM_GB`` beside them, so they are mapped from the pack's ``experts.bin``
    instead of copied into RAM, and the mode writes an ``arena_gb + 1`` GB arena to disk
    (setup.py:6418-6420). Unsloth's ``budget: True`` rows are exempt (setup.py:4051): they read
    part of their experts from the GGUF through ``--resident-budget-gib`` whatever the RAM is, so
    no arena is written and the low-RAM mode is not what runs there.
    """
    if tier["family"] == "unsloth":
        return False
    return ram_gb < tier["arena_gb"] + LOW_RAM_HEADROOM_GB


def fits(box: dict, tier: dict) -> tuple[bool, bool]:
    """``(fits_in_ram, needs_low_ram_mode)`` — could this tier run here, and how degraded?

    The permissive predicate: ``fits_in_ram`` is True when the box can hold the tier at all,
    either at its published floor (``ram_gb``) or with the experts mapped from disk once the arena
    fits (``arena_gb``). ``needs_low_ram_mode`` is upstream's own answer
    (:func:`_low_ram_applies`), so it is True for a tier that clears its floor but leaves no room
    beside the experts — the band where the mode applies to a tier this tool will still recommend.

    :func:`recommend` is stricter about which tiers it will *name* (see the module docstring), so
    ``fits()`` answering True is not by itself a recommendation.
    """
    ram = _gb(box.get("ram_mib", 0))
    return (ram >= tier["ram_gb"] or ram >= tier["arena_gb"]), _low_ram_applies(ram, tier)


def _disk_gb(name: str, low_ram: bool, shared_shard2_held: bool) -> float | None:
    """GB that must be free in the target directory to install ``name``.

    Built on ``catalog.download_bytes()``, never on ``download_gb``: on the four abliterated tiers
    the published figure describes ISTA's censored file and sits 0.26-0.47 GB under what SC117's
    re-quantization actually lands, and a preflight built on the smaller number waves through a
    download the drive cannot hold. The shared shard 2 is subtracted when the box already holds
    it, and the low-RAM mode's arena (``arena_gb + 1``, setup.py:6418-6420) is added when it applies.
    """
    total = catalog.download_bytes(name)
    if total is None:
        return None
    gb = total / GB
    if shared_shard2_held and catalog.TIERS[name]["abliterated"]:
        gb -= catalog.shared_shard2()[0] / GB
    if low_ram:
        gb += catalog.TIERS[name]["arena_gb"] + 1
    return round(gb, 1)


def _ceiling(ram_gb: float, tier: dict, context: int, kv: str) -> tuple[str, str]:
    """Which ceiling binds, and the flag that relieves it (spec §9: one that drives no flag is
    decoration).

    VRAM first: if the ladder had to come down from the trained window, the card is what limited
    the install, and ``--max-context`` / ``--kv`` are what buy it back. Otherwise host RAM: under
    upstream's own 24 GB of room beside the model there is no conversation cache to size, and
    ``--conversation-cache-mib`` / ``--conversation-cache-slots`` are what shrink it.
    """
    if context < TRAINED_CONTEXT:
        return "vram", f"--max-context {context} --kv {kv}"
    if ram_gb - tier["ram_gb"] < AGENT_CACHE_FREE_GB:
        return "ram", "--conversation-cache-mib / --conversation-cache-slots"
    return "none", ""


def _rank_key(name: str, want_abliterated: bool) -> tuple[int, float]:
    """Ranking key, highest best: the uncensored build when it was asked for and exists at a tier
    that fits, then upstream's own quality proxy — the largest download that fits
    (setup.py:2247-2249, ``max(fitting, key=lambda m: MODELS[m]["download_gb"])``)."""
    tier = catalog.TIERS[name]
    return (1 if want_abliterated and tier["abliterated"] else 0, tier["download_gb"])


def _stop_reason(box: dict) -> str:
    """Why there is no tier at all, or ``""`` when the box can be sized."""
    if not box.get("gpu_supported"):
        return ("no tier recommended: this tool sizes tiers for NVIDIA and Apple Silicon only, and "
                f"this box is neither ({box.get('gpu_note') or 'no GPU reported'}). Strata itself "
                "supports AMD and Intel; this tool cannot size them, and sizing a tier from a "
                "vram_mib of 0 would be a guess rather than a fit.")
    if not box.get("ram_mib"):
        return ("no tier recommended: the RAM reading came back as 0 MiB, so no tier's RAM floor "
                "can be checked. On Linux that is the /proc/meminfo read failing (no awk?); every "
                "tier here is sized from RAM first, so 0 MiB gets no tier rather than a confident "
                "one.")
    if not box.get("vram_mib"):
        return ("no tier recommended: the GPU reported 0 MiB of VRAM, so no context can be sized "
                "for it. The engine stops with 'no VRAM is left for the expert cache' before it "
                "answers a request.")
    return ""


def _floor_reason(ram_gb: float, names: list[str]) -> str:
    """Why nothing fits, naming the floors the box is under — a correct answer, not a silent no
    (spec §4)."""
    by_floor = sorted(names, key=lambda n: (catalog.TIERS[n]["ram_gb"], catalog.TIERS[n]["download_gb"]))
    lowest = by_floor[0]
    abl = [n for n in names if catalog.TIERS[n]["abliterated"]]
    smallest_abl = min(abl, key=lambda n: catalog.TIERS[n]["ram_gb"]) if abl else None
    line = (f"no tier this tool offers fits {ram_gb:.1f} GB of RAM.")
    if smallest_abl:
        line += (f" The smallest uncensored build, {smallest_abl}, needs "
                 f"{catalog.TIERS[smallest_abl]['ram_gb']} GB")
        if smallest_abl != lowest:
            line += (f"; the smallest build of any kind, the censored {lowest}, needs "
                     f"{catalog.TIERS[lowest]['ram_gb']} GB")
        line += "."
    else:
        line += (f" The smallest build this installer offers, {lowest}, needs "
                 f"{catalog.TIERS[lowest]['ram_gb']} GB.")
    return line + " Add RAM, or run this model on a bigger box."


def _reason(box: dict, name: str, context: int, kv: str, kv_fits: bool, low_ram: bool,
            disk_gb: float | None, ceiling: str, relief: str, sessions: int,
            want_abliterated: bool) -> str:
    """The arithmetic, in the order a user reads it: what fits, what it costs, what limits it."""
    tier = catalog.TIERS[name]
    ram, vram = _gb(box.get("ram_mib", 0)), _gb(box.get("vram_mib", 0))
    room = _kv_room(vram, tier)
    parts = [f"{name}: {tier['ram_gb']} GB of RAM keeps its experts resident and this box reports "
             f"{ram:.1f} GB."]
    if not tier["abliterated"]:
        why = "none of them fits this box" if want_abliterated else "you asked for the censored model"
        parts.append(f"This is the censored model: the uncensored builds exist at "
                     f"{', '.join(catalog.abliterated_tiers())} only, and {why}.")
        if tier["family"] == "coder":
            parts.append("It is also half the experts - 256 of 512, the ones code, tools and vision "
                         "use - so it is a different model, not a smaller build of the same one.")
    measured = (NOT_MEASURED if tier["family"] not in MEASURED_FAMILY else "")
    if kv_fits:
        parts.append(f"{vram:.1f} GB of VRAM leaves {room:.1f} GB beside the expert cache it still "
                     f"has to hold: a context of {context} tokens at --kv {kv} costs "
                     f"{_kv_gb(context, kv):.1f} GB of KV at the measured "
                     f"{KV_BYTES_PER_TOKEN_INT8:,} bytes/token{measured}.")
    else:
        parts.append(f"The card is the problem before the context is: {vram:.1f} GB less the ~"
                     f"{DENSE_WEIGHTS_GB:.0f} GB of dense weights and buffers leaves "
                     f"{max(0.0, vram - DENSE_WEIGHTS_GB):.1f} GB, and {EXPERT_CACHE_HELD:.0%} of "
                     f"its {tier['arena_gb']} GB expert arena has to stay beside the KV cache — "
                     f"short by {-room:.1f} GB even at the shortest context on the ladder, at the "
                     f"measured {KV_BYTES_PER_TOKEN_INT8:,} bytes/token{measured}.")
    if low_ram:
        parts.append(f"Its experts do not fit that RAM with the {LOW_RAM_HEADROOM_GB} GB upstream "
                     f"keeps beside them, so the installer writes a "
                     f"{tier['arena_gb'] + 1:.1f} GB expert arena to disk (the low-RAM mode).")
    if ceiling == "vram" and kv_fits:
        parts.append(f"VRAM is the ceiling: {context} is the longest context whose KV cache fits "
                     f"beside the expert cache, and {relief} is what buys more.")
    elif ceiling == "vram":
        parts.append(f"VRAM is the ceiling, and a hard one: the engine stops with 'no VRAM is left "
                     f"for the expert cache' unless it runs at the floor of the ladder, so {relief} "
                     f"is not a tuning choice on this card but what it allows.")
    elif ceiling == "ram":
        parts.append(f"Host RAM is the ceiling: {ram - tier['ram_gb']:.1f} GB is left beside the "
                     f"model, under the {AGENT_CACHE_FREE_GB} GB upstream treats as room for a "
                     f"conversation cache, so the {sessions}-session cache has to come down with "
                     f"{relief}.")
    else:
        parts.append(f"Neither ceiling binds: {context} at --kv {kv} fits the VRAM and "
                     f"{ram - tier['ram_gb']:.1f} GB is left beside the model for the "
                     f"{sessions}-session cache.")
    if disk_gb is not None:
        note = ""
        if tier["abliterated"]:
            note = (f" — the real size of its two shards, not upstream's "
                    f"{tier['download_gb']} GB figure for the censored file")
        if box.get("has_shared_shard2") and tier["abliterated"]:
            note += f", less the {catalog.shared_shard2()[0] / GB:.1f} GB shard 2 it already holds"
        parts.append(f"{disk_gb} GB has to be free to install it{note}.")
    return " ".join(parts)


def _plan(box: dict, name: str, sessions: int, want_abliterated: bool) -> dict:
    """One answer, in the shape Task 7 prints."""
    tier = catalog.TIERS[name]
    ram, vram = _gb(box.get("ram_mib", 0)), _gb(box.get("vram_mib", 0))
    context, kv, kv_fits = _kv_plan(vram, tier)
    low_ram = _low_ram_applies(ram, tier)
    disk_gb = _disk_gb(name, low_ram, bool(box.get("has_shared_shard2")))
    ceiling, relief = _ceiling(ram, tier, context, kv)
    return {"tier": name, "abliterated": tier["abliterated"],
            "reason": _reason(box, name, context, kv, kv_fits, low_ram, disk_gb, ceiling, relief,
                              sessions, want_abliterated),
            "context": context, "kv": kv, "low_ram": low_ram, "extra_disk_gb": disk_gb,
            "ceiling": ceiling, "relief": relief, "alternatives": []}


def _no_tier(reason: str) -> dict:
    """The same shape with nothing in it, so a caller can print one answer either way."""
    return {"tier": None, "abliterated": False, "reason": reason, "context": None, "kv": None,
            "low_ram": False, "extra_disk_gb": None, "ceiling": "none", "relief": "",
            "alternatives": []}


def recommend(box: dict, sessions: int = 4, want_abliterated: bool = True) -> dict:
    """Recommend a tier for ``box``, the best-fitting answer first and the rest ranked behind it.

    ``box`` is :func:`lib.detect.detect`'s dict. ``sessions`` is how many conversations the user
    wants warm at once, which sizes the conversation cache the RAM ceiling governs.
    ``want_abliterated`` is the tool's whole purpose by default: an uncensored build is preferred
    whenever one fits, and when none does the answer is a censored tier that says so out loud
    rather than one that quietly substitutes (spec §8).

    ``tier`` is None on a stop or when the box is under every published RAM floor; ``reason`` then
    says which floor it was under. ``ceiling`` is ``"vram"``, ``"ram"`` or ``"none"`` and
    ``relief`` names the flag that relieves it, or ``""`` when nothing binds. ``alternatives``
    holds the same shape, ranked, with its own ``alternatives`` empty — the ranking lives on the
    top answer.
    """
    stop = _stop_reason(box)
    if stop:
        return _no_tier(stop)
    names = catalog.tiers_for(box.get("os") or "linux")
    ram = _gb(box["ram_mib"])
    fitting = [n for n in names if ram >= catalog.TIERS[n]["ram_gb"]]
    if not fitting:
        return _no_tier(_floor_reason(ram, names))
    ranked = sorted(fitting, key=lambda n: _rank_key(n, want_abliterated), reverse=True)
    plans = [_plan(box, n, sessions, want_abliterated) for n in ranked]
    best, alternatives = plans[0], plans[1:]
    best["alternatives"] = alternatives
    return best
