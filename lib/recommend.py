"""Which tier this box can run, and what each ceiling costs to relieve.

This is the answer to the question the whole tool exists to answer (spec §1): *can this machine
run this model, and which build of it?* It takes a box from :func:`lib.detect.detect` and returns
a recommendation with the arithmetic in the ``reason`` the user reads, plus the same shape ranked
behind it as ``alternatives``. Nothing is fetched and nothing is run: every figure is already in
the box dict and in :mod:`lib.catalog`, so the tool can answer with no network (spec §16).

Units: two conversions, because upstream uses two
    **RAM and VRAM are GiB.** ``ram_gb()`` (setup.py:582-588) returns ``int(MemTotal_kB) * 1024 /
    2**30`` on Linux and ``ullTotalPhys / 2**30`` on Windows — both GiB — and upstream compares
    that figure against ``MODELS[m]["ram_gb"]`` with no conversion at all (setup.py:2245 :2247
    :6092). ``gpu["vram_gb"]`` is nvidia-smi's MiB over 1024 (setup.py:1002), also GiB. A
    ``*_mib`` reading therefore becomes comparable by ``/ 1024``, in :func:`_ram_gib` and
    :func:`_vram_gib`. The single ``* 1048576 / 1e9`` this file used before reported the same
    memory 7.4 % larger — 1 GiB = 1.073741824 GB, the ratio upstream divides a *byte-derived*
    figure by when it does need the comparison (``MODELS[model]["arena_gb"] / 1.073741824``,
    setup.py:1875 :1892 :6201) and never applies to ``ram_gb`` — so every RAM floor here sat 7.4 %
    lower than upstream's.

    **Disk and KV bytes are decimal GB.** ``catalog.download_bytes() / 1e9`` and the low-RAM mode's
    ``arena_gb + 1`` are what upstream compares against ``free_gb()`` =
    ``shutil.disk_usage().free / 1e9`` (setup.py:1596-1598, the preflight's ``need`` at
    :6418-6420), and upstream's own KV rates are byte counts over ``1e9`` too (setup.py:4020
    :4061). :func:`_disk_gb` and :func:`_kv_gb` stay decimal for that reason, and ``_reason``
    prints GiB for RAM and VRAM and GB for the byte-derived figures.

    setup.py:292 and :297 are the proof that the two kinds of column are not the same unit: one
    figure printed twice, ``"63.3 GiB (68.0 GB) of experts"`` in the comment above the NVFP4 row
    that reads ``"arena_gb": 68.0``.

    The reference box decides the argument, and it is not the case the first version of this file
    thought it was. Read on this box on 2026-10-17 its ``MemTotal`` is 61,935,996 kB = 60,484 MiB
    = **59.07 GiB**, 2.93 GiB *under* IQ3_S's 62 GiB floor. Upstream's verdict for it is not "fits"
    but "tight" (setup.py:6092), and ``reference/strata-iq3_s-abliterated.json`` carries
    ``--resident-experts``, which upstream writes only from ``low_ram_resident`` (setup.py:6329
    :6336-6337, emitted at :6591): the box runs IQ3_S in the low-RAM mode, with the experts the two
    cards do not hold copied from the pack. The old decimal reading (63.42 GB, over the 62 floor)
    reached the same tier by the wrong route and called the answer "fits".

Two predicates, and the three-state verdict they both come from
    Upstream's ``--check`` ladder (setup.py:6092) is not a boolean: ``"fits" if ram >= ram_gb else
    "tight" if ram >= ram_gb - 8 else "does not fit"``, and setup.py:6111 counts anything that is
    not "does not fit" as fitting. Both predicates here answer that ladder. :func:`fits` is the
    permissive one — it also takes upstream's low-RAM-mode test, where the card's VRAM makes up the
    difference (``low_ram_fits``, setup.py:4039-4042) — and :func:`recommend` answers the same
    ladder for the tiers this tool will *name*, without the VRAM help. A tier inside the 8 GB band
    is labelled **tight** in the ``reason``, because "tight" is a materially different answer from
    "fits"; it lives in the text, not in a key of its own (Task 7 prints a fixed shape).

Stops, not zeros (spec §12)
    ``gpu_supported`` False, ``ram_mib`` 0 or ``vram_mib`` 0 returns no tier at all. A Linux box
    with no ``awk`` reports ``ram_mib: 0`` beside ``gpu_supported: True``, and a card can report 0
    MiB; sizing either from that zero is the guess §12 exists to prevent, and every tier's floor
    minus the tight band is above a zero anyway.

Every number here names its source:
    17,406 bytes/token at ``--kv int8``   measured (spec §9); the per-format scale is the engine's
                                          own per-cell bytes — 1056 for int8, 816 for k8v4, 576
                                          for q4_0 (setup.py:198 and its ``--kv`` help), which is
                                          the check spec §9 names
    ``--kv`` choices                      setup.py:5722
    ~5 GB of VRAM for the dense weights,
    buffers and a short context           setup.py:4017-4023 (``low_ram_gpu_gb``)
    what a card holds of the expert arena setup.py:4017-4023 (``low_ram_gpu_gb``), reproduced by
                                          :func:`_expert_cache_gb`; the engine stops with "no VRAM
                                          is left for the expert cache" when it reaches 0
                                          (setup.py:5095-5097, :5139-5147)
    the 50 % of the arena the card has to
    keep beside the KV, for a context to
    be worth recommending                 THIS TOOL'S OWN rule (``EXPERT_CACHE_MIN_SHARE``), not
                                          upstream's. The 0.5 is borrowed from setup.py:5155
                                          (``PARALLEL_HELD``), which is the share the cache must
                                          still hold before ``parallel N`` may take VRAM the cache
                                          would hold (#465) — it says nothing about the KV cache,
                                          and upstream gives the cache whatever the KV leaves
                                          rather than holding a share back. This tool recommends a
                                          context, so it needs a bar to step down to; see
                                          :func:`_kv_room`
    the low-RAM mode's 6 GiB of RAM       setup.py:4042 (the literal in ``low_ram_fits``), the RAM
                                          upstream leaves out of the way beside the experts
    the 8 GiB tight band                  setup.py:6092 (``"tight" if ram >= ram_gb - 8``), counted
                                          as fitting by setup.py:6111
    10 GB beside the experts              setup.py:3999 (``LOW_RAM_HEADROOM_GB``), the test at
                                          setup.py:4011-4013 that decides the low-RAM mode applies
    arena + 1 GB of extra disk            setup.py:6418-6420 (the disk preflight's ``need``)
    24 GB beside the model                setup.py:5210 (``AGENT_CACHE_FREE_GB``, bench #882 #440),
                                          upstream's own line for "this box has room for a
                                          conversation cache"
    262144                                the trained window (spec §8/§9); 204800 is a menu step
                                          upstream never recommends (#608, setup.py:4237) and
                                          393216/524288 sit above the trained window, so the
                                          ladder stops at 262144
    the four abliterated tiers            spec §8; ``catalog.abliterated_tiers()``
    ``--context``, and nothing else, on
    a Mac                                 metal/setup_mac.py:488-506 — its whole ``add_argument``
                                          set is api-key, build, check, context, data-dir,
                                          draft-vocab, family, gguf-dir, host, model, models-dir,
                                          no-browser, no-start, port, setup, update, vision, yes.
                                          No ``--max-context``, no ``--kv``, no
                                          ``--conversation-cache-*``

``sessions`` sizes the conversation cache, which is the RAM ceiling's subject; the per-session
snapshot cost is measured against a running engine (``estimate_snapshot_mib`` /
``slots_for_budget``, added next), so here it appears in the ceiling's text rather than in the
tier choice — RAM caps how many sessions stay warm, it does not change which tier is resident.

``box["has_shared_shard2"]`` (optional, falsy by default) says the target directory already holds
the 28.8 GB shard 2 that every abliterated tier shares (``catalog.shared_shard2()``); the disk
figure then counts it once, not twice (spec §16). The relief flags named are the arguments the
installer for that OS can actually take: on macOS the menu is narrower than the config's (spec
§8), and a ceiling it has no flag for is not reported as a ceiling (spec §9).
"""

from lib import catalog

# Measured, not modelled: snapshot_bytes 3,892,476,616 / 223,627 tokens at --kv int8 and 262144
# on the reference box (spec §9). Everything downstream scales from this one figure.
KV_BYTES_PER_TOKEN_INT8 = 17_406

# The engine's per-cell bytes, which is how a format other than int8 scales the measured rate
# (setup.py:198 for q4_0 and k8v4; int8's 1056 is the engine's own help). The three formats are
# upstream's --kv choices (setup.py:5722); the order here is best quality first by those per-cell
# bytes — int8, k8v4, q4_0 — which is the order a user trades them in, not upstream's list order.
KV_CELL_BYTES = {"int8": 1056, "k8v4": 816, "q4_0": 576}
KV_FORMATS = ("int8", "k8v4", "q4_0")

# The trained window. Above it the context needs rope scaling and no KV figure here was measured
# there, so it is the top of the ladder this tool recommends rather than a step it walks.
TRAINED_CONTEXT = 262_144

# Upstream's ladder (setup.py:381, verbatim in catalog.CONTEXTS) cut to what this tool will
# recommend: nothing above the trained window, and 204800 dropped because #608 says the 200K menu
# step is never the recommendation.
CONTEXT_LADDER = tuple(c for c in catalog.CONTEXTS if c <= TRAINED_CONTEXT and c != 204_800)

# setup.py:4017-4023: what a card holds before any expert cache is counted — the dense weights,
# buffers and a context this long are inside the ~5 GB. It is low_ram_gpu_gb's own default ctx.
DENSE_WEIGHTS_GB = 5.0
KV_BASELINE_CTX = 32_768

# This tool's own bar for recommending a context, not upstream's rule (see the module docstring).
# The 0.5 is the figure at setup.py:5155 (PARALLEL_HELD), where it is the share of the experts the
# cache must still hold before parallel N may take VRAM the cache would hold (#465) — nothing to do
# with the KV cache. Upstream models the cache as whatever the KV leaves (low_ram_gpu_gb,
# setup.py:4017-4023) and stops only when nothing is left; a tool that recommends a context needs a
# share to step the ladder down to, and this is the one upstream has published for the cache.
EXPERT_CACHE_MIN_SHARE = 0.5

# setup.py:3999 / :4011-4013: the RAM the experts need beside them, and the test that puts the
# installer into the low-RAM mode when it is not there.
LOW_RAM_HEADROOM_GB = 10

# setup.py:4042: the RAM low_ram_fits leaves out of the way before it counts the RAM as room for
# the experts the card does not hold. Upstream's literal, kept as a name because the arithmetic is
# in this file's fits() and a bare 6 in that expression is untraceable.
LOW_RAM_FITS_RAM_GB = 6

# setup.py:6092: the band under a tier's published floor that upstream's verdict calls "tight"
# rather than "does not fit", and which setup.py:6111 counts as fitting.
TIGHT_BAND_GB = 8

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


def _ram_gib(mib: int) -> float:
    """A ``ram_mib`` reading in the GiB upstream's ``ram_gb()`` returns and compares floors with.

    setup.py:582-588: ``int(MemTotal_kB) * 1024 / 2**30`` on Linux, ``ullTotalPhys / 2**30`` on
    Windows — GiB both times — and setup.py:2245 :2247 :6092 compare it against
    ``MODELS[m]["ram_gb"]`` with no conversion. The step is ``/ 1024``; the decimal-GB step
    ``* 1048576 / 1e9`` reports the same memory 7.4 % larger and opens every floor by that much.
    """
    return mib / 1024


def _vram_gib(mib: int) -> float:
    """A ``vram_mib`` reading in the GiB upstream's ``gpu["vram_gb"]`` is in.

    setup.py:1002 turns nvidia-smi's ``memory.total`` MiB into ``float(mem) / 1024.0``, and every
    VRAM test downstream uses that GiB figure (:4017-4023, :4039-4042). Same step as
    :func:`_ram_gib`, named separately because the two readings come from different fields and the
    two gates are different questions.
    """
    return mib / 1024


def _kv_bytes_per_token(kv: str) -> int:
    """Bytes of KV one context token costs in format ``kv``, from the measured int8 rate.

    The scale is the engine's own per-cell bytes (setup.py:198 and its ``--kv`` help), which is the
    check spec §9 names. It is above upstream's modelled rate — see :func:`_expert_cache_gb`.
    """
    return KV_BYTES_PER_TOKEN_INT8 * KV_CELL_BYTES[kv] / KV_CELL_BYTES["int8"]


def _kv_gb(context: int, kv: str) -> float:
    """GB of VRAM one conversation's KV cache takes at ``context`` tokens in format ``kv``.

    Decimal GB, because the figure is a byte count: upstream's own KV rates are bytes over ``1e9``
    too (``kv_streaming_ram_gb``, setup.py:4056-4061, and the ``longer`` term of
    ``low_ram_gpu_gb`` at :4020), and those are what the VRAM tests subtract from a GiB reading.
    """
    return context * _kv_bytes_per_token(kv) / GB


def _kv_room(vram_gib: float, tier: dict) -> float:
    """GB of VRAM the KV cache may take: the card less the dense weights, less the share of the
    expert arena this tool keeps room for. Negative when the card cannot hold both.

    The share is :data:`EXPERT_CACHE_MIN_SHARE`, this tool's own bar — see its comment. What
    upstream computes here is the other way round (the cache gets the remainder,
    ``low_ram_gpu_gb``, setup.py:4017-4023), and :func:`_expert_cache_gb` reproduces that. Like
    upstream, this subtracts a decimal-GB arena from a GiB card (:4022 does the same).
    """
    return max(0.0, vram_gib - DENSE_WEIGHTS_GB) - EXPERT_CACHE_MIN_SHARE * tier["arena_gb"]


def _expert_cache_gb(vram_gib: float, tier: dict, context: int, kv: str) -> float:
    """GB of the tier's expert arena the card holds at ``(context, kv)`` — upstream's model.

    ``low_ram_gpu_gb`` (setup.py:4017-4023): the VRAM less ~5 GB of dense weights, buffers and a
    32K context, less the KV of a longer context, capped at the arena. When this reaches 0 the
    engine stops with "no VRAM is left for the expert cache" (setup.py:5095-5097, and the ~8 GB
    card note at :5139-5147), so it is also the test for "does this card run at all".

    Upstream models the longer-context KV at ``13 * cell_bytes`` B/token — 13,728 at int8
    (setup.py:4020, and ``kv_streaming_ram_gb`` at :4061). This tool substitutes its own measured
    17,406 B/token instead. Measured beating modelled is fine, but they are two different figures:
    17,406 is a snapshot read off a running engine (spec §9), 13,728 is 13 layers times the
    engine's per-cell bytes. The measured rate is the larger, so the share reported here is the
    more conservative of the two — on a 24 GiB card with IQ3_S at 131072/int8, 17.3 GB of the
    50.3 GB arena here against upstream's 17.7 GB.
    """
    longer = max(0, context - KV_BASELINE_CTX) * _kv_bytes_per_token(kv) / GB
    return max(0.0, min(tier["arena_gb"], vram_gib - DENSE_WEIGHTS_GB - longer))


def _kv_plan(vram_gib: float, tier: dict) -> tuple[int, str, bool, bool]:
    """``(context, kv, meets_share, runs_on_card)`` — the longest context whose KV fits this
    VRAM, and the format that buys it.

    Walks ``CONTEXT_LADDER`` down from the trained window at the best format, then the next format
    down — the order a user would trade them in, and the two flags Linux's ``relief`` names. Where
    no rung leaves the card :data:`EXPERT_CACHE_MIN_SHARE` of the arena, the answer is the floor of
    the ladder with ``meets_share`` False: the same direction upstream takes on a ~8 GB card, which
    drops to 4-bit KV and a 4608 context before it gives the draft layer up (setup.py:5139-5147,
    ``UD_FIT_CTX_STEPS``). Whether even that runs is upstream's question, not this tool's —
    ``runs_on_card`` is :func:`_expert_cache_gb` at the floor, and a card that holds no experts at
    all is the one case allowed to say the engine stops.
    """
    room = _kv_room(vram_gib, tier)
    for kv in KV_FORMATS:
        for context in reversed(CONTEXT_LADDER):
            if _kv_gb(context, kv) <= room:
                return context, kv, True, True
    floor_context, floor_kv = CONTEXT_LADDER[0], KV_FORMATS[-1]
    return (floor_context, floor_kv, False,
            _expert_cache_gb(vram_gib, tier, floor_context, floor_kv) > 0.0)


def _low_ram_fits(ram_gib: float, vram_gib: float, tier: dict) -> bool:
    """Upstream's own low-RAM-mode test (``low_ram_fits``, setup.py:4039-4042): the experts the
    card does not hold fit the RAM left beside the rest (as file cache).

    ``ram - 6 + max(0.0, vram_gb - 5) >= arena_gb``, verbatim: the 6 is upstream's literal
    (:data:`LOW_RAM_FITS_RAM_GB`) and the card's side is its VRAM less the ~5 GB of dense weights
    (:data:`DENSE_WEIGHTS_GB`). Both sides are GiB, and ``arena_gb`` is the decimal column — which
    is how upstream writes it at :4042, with no ``/ 1.073741824``.
    """
    return ram_gib - LOW_RAM_FITS_RAM_GB + max(0.0, vram_gib - DENSE_WEIGHTS_GB) >= tier["arena_gb"]


def _low_ram_applies(ram_gib: float, tier: dict) -> bool:
    """Would the installer put this tier in the low-RAM mode on ``ram_gib`` GiB of RAM?

    Upstream's own test (``low_ram_needed``, setup.py:4011-4013): the experts do not fit with the
    usual ``LOW_RAM_HEADROOM_GB`` beside them, so they are mapped from the pack's ``experts.bin``
    instead of copied into RAM, and the mode writes an ``arena_gb + 1`` GB arena to disk
    (setup.py:6418-6420). The RAM here is GiB — upstream's unit for this comparison, and the arena
    column is compared with it unconverted, exactly as at :4013. Unsloth's ``budget: True`` rows
    are exempt (setup.py:4051): they read part of their experts from the GGUF through
    ``--resident-budget-gib`` whatever the RAM is, so no arena is written and the low-RAM mode is
    not what runs there.
    """
    if tier["family"] == "unsloth":
        return False
    return ram_gib < tier["arena_gb"] + LOW_RAM_HEADROOM_GB


def fits(box: dict, tier: dict) -> tuple[bool, bool]:
    """``(fits_in_ram, needs_low_ram_mode)`` — could this tier run here, and how degraded?

    The permissive predicate, and it is upstream's three-state verdict rather than a floor test:
    True when the box clears the tier's published floor, or sits inside the 8 GiB band upstream
    calls tight (setup.py:6092, counted as fitting at :6111), or the low-RAM mode closes the gap
    with the card's VRAM — ``low_ram_fits``, setup.py:4039-4042, reproduced by
    :func:`_low_ram_fits`. That last branch is what makes a 40 GiB box with a 24 GiB card a "fits
    in the low-RAM mode" answer: ``40 - 6 + 19 = 53`` against IQ3_S's 50.3 GB arena. It is also
    what refuses a 48.8 GiB box with a 6 GiB card, where upstream computes ``48.8 - 6 + 1 = 43.8``
    and says nothing fits.

    ``needs_low_ram_mode`` is upstream's own answer (:func:`_low_ram_applies`), so it is True for a
    tier that clears its floor but leaves no room beside the experts — the band where the mode
    applies to a tier this tool will still recommend.

    :func:`recommend` names fewer tiers than this predicate accepts (see the module docstring), so
    ``fits()`` answering True is not by itself a recommendation.
    """
    ram, vram = _ram_gib(box.get("ram_mib", 0)), _vram_gib(box.get("vram_mib", 0))
    return (ram >= tier["ram_gb"] - TIGHT_BAND_GB or _low_ram_fits(ram, vram, tier),
            _low_ram_applies(ram, tier))


def _disk_gb(name: str, low_ram: bool, shared_shard2_held: bool) -> float | None:
    """GB that must be free in the target directory to install ``name``.

    Built on ``catalog.download_bytes()``, never on ``download_gb``: on the four abliterated tiers
    the published figure describes ISTA's censored file and sits 0.26-0.47 GB under what SC117's
    re-quantization actually lands, and a preflight built on the smaller number waves through a
    download the drive cannot hold. The shared shard 2 is subtracted when the box already holds
    it, and the low-RAM mode's arena (``arena_gb + 1``, setup.py:6418-6420) is added when it applies.

    Decimal GB throughout, because that is the unit of the figure it is compared with: upstream's
    ``free_gb()`` is ``shutil.disk_usage().free / 1e9`` (setup.py:1596-1598) and the ``need`` it is
    checked against at :6418-6420 is byte counts over ``1e9`` plus ``arena_gb + 1``.
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


def _ceiling(os_name: str, ram_gib: float, tier: dict, context: int, kv: str) -> tuple[str, str]:
    """Which ceiling binds, and the flag the installer for this OS can set to relieve it (spec §9:
    one that drives no flag is decoration).

    VRAM first: if the ladder had to come down from the trained window, the card is what limited
    the install. On Linux ``--max-context`` and ``--kv`` are what buy it back. On macOS only
    ``--context`` is: metal/setup_mac.py's whole ``add_argument`` set is api-key, build, check,
    context, data-dir, draft-vocab, family, gguf-dir, host, model, models-dir, no-browser,
    no-start, port, setup, update, vision, yes (:488-506), so it has no ``--max-context``, no
    ``--kv`` and no conversation-cache flag. Naming a flag that installer cannot set is worse than
    naming none, so on a Mac the KV format is not offered as relief and a RAM ceiling — which only
    the conversation-cache flags move — is not reported as a ceiling at all. ``_reason`` still says
    the RAM is tight.

    Otherwise host RAM: under upstream's own 24 GB of room beside the model there is no
    conversation cache to size, and ``--conversation-cache-mib`` / ``--conversation-cache-slots``
    are what shrink it.
    """
    if context < TRAINED_CONTEXT:
        if os_name == "darwin":
            return "vram", f"--context {context}"
        return "vram", f"--max-context {context} --kv {kv}"
    if ram_gib - tier["ram_gb"] < AGENT_CACHE_FREE_GB:
        if os_name == "darwin":
            return "none", ""
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


def _floor_reason(ram_gib: float, names: list[str]) -> str:
    """Why nothing fits, naming the floors the box is under — a correct answer, not a silent no
    (spec §4). The floors are quoted where upstream stops, i.e. 8 GiB below the published figure
    (setup.py:6092), because a box inside that band does get a tier from this tool."""
    by_floor = sorted(names, key=lambda n: (catalog.TIERS[n]["ram_gb"], catalog.TIERS[n]["download_gb"]))
    lowest = by_floor[0]
    abl = [n for n in names if catalog.TIERS[n]["abliterated"]]
    smallest_abl = min(abl, key=lambda n: catalog.TIERS[n]["ram_gb"]) if abl else None
    line = (f"no tier this tool offers fits {ram_gib:.1f} GiB of RAM: it sits more than the "
            f"{TIGHT_BAND_GB} GiB band upstream's verdict calls tight below every floor here, which "
            f"is where that verdict says \"does not fit\" (setup.py:6092).")
    if smallest_abl:
        line += (f" The smallest uncensored build, {smallest_abl}, needs "
                 f"{catalog.TIERS[smallest_abl]['ram_gb']} GiB")
        if smallest_abl != lowest:
            line += (f"; the smallest build of any kind, the censored {lowest}, needs "
                     f"{catalog.TIERS[lowest]['ram_gb']} GiB")
        line += (f" — and this tool stops where upstream does, {TIGHT_BAND_GB} GiB below those "
                 "figures.")
    else:
        line += (f" The smallest build this installer offers, {lowest}, needs "
                 f"{catalog.TIERS[lowest]['ram_gb']} GiB.")
    return line + " Add RAM, or run this model on a bigger box."


def _beside_model(ram_gib: float, tier: dict) -> str:
    """How much RAM is left beside the model, in words. Negative when the tier's own floor already
    sits above what the box has — which is the tight band, and worth saying rather than printing a
    minus sign a user has to interpret."""
    beside = ram_gib - tier["ram_gb"]
    if beside >= 0:
        return f"{beside:.1f} GiB is left beside the model"
    return (f"the tier's own floor is already {-beside:.1f} GiB above what this box has, so nothing "
            f"is left beside the model")


def _reason(box: dict, name: str, context: int, kv: str, meets_share: bool, runs_on_card: bool,
            low_ram: bool, disk_gb: float | None, ceiling: str, relief: str, sessions: int,
            want_abliterated: bool) -> str:
    """The arithmetic, in the order a user reads it: what fits, what it costs, what limits it."""
    tier = catalog.TIERS[name]
    os_name = box.get("os") or "linux"
    ram, vram = _ram_gib(box.get("ram_mib", 0)), _vram_gib(box.get("vram_mib", 0))
    room, held = _kv_room(vram, tier), _expert_cache_gb(vram, tier, context, kv)
    tight = ram < tier["ram_gb"]
    parts = [f"{name}: {tier['ram_gb']} GiB of RAM keeps its experts resident and this box reports "
             f"{ram:.1f} GiB."]
    if tight:
        parts.append(f"That is {tier['ram_gb'] - ram:.1f} GiB under the floor — inside the "
                     f"{TIGHT_BAND_GB} GiB band upstream's own verdict calls \"tight\" rather than "
                     f"\"does not fit\" (setup.py:6092), so it runs with less room beside the tier "
                     f"than the tier wants.")
    if not tier["abliterated"]:
        why = "none of them fits this box" if want_abliterated else "you asked for the censored model"
        parts.append(f"This is the censored model: the uncensored builds exist at "
                     f"{', '.join(catalog.abliterated_tiers())} only, and {why}.")
        if tier["family"] == "coder":
            parts.append("It is also half the experts - 256 of 512, the ones code, tools and vision "
                         "use - so it is a different model, not a smaller build of the same one.")
    measured = (NOT_MEASURED if tier["family"] not in MEASURED_FAMILY else "")
    if meets_share:
        parts.append(f"{vram:.1f} GiB of VRAM leaves {room:.1f} GB beside the share of the expert "
                     f"arena this tool keeps room for: a context of {context} tokens at --kv {kv} "
                     f"costs {_kv_gb(context, kv):.1f} GB of KV at the measured "
                     f"{KV_BYTES_PER_TOKEN_INT8:,} bytes/token{measured}.")
    elif runs_on_card:
        parts.append(f"The card is the tight side before the context is: at {context} tokens it "
                     f"holds {held:.1f} of the tier's {tier['arena_gb']} GB expert arena (the "
                     f"remainder after the dense weights and this context's KV, upstream's model at "
                     f"setup.py:4017-4023), short of the {EXPERT_CACHE_MIN_SHARE:.0%} this tool "
                     f"sizes against, so the rest of the experts come from RAM and the SSD. The KV "
                     f"itself costs {_kv_gb(context, kv):.1f} GB at the measured "
                     f"{KV_BYTES_PER_TOKEN_INT8:,} bytes/token{measured}.")
    else:
        parts.append(f"The card is the problem before the context is: {vram:.1f} GiB less the ~"
                     f"{DENSE_WEIGHTS_GB:.0f} GB of dense weights and buffers leaves "
                     f"{max(0.0, vram - DENSE_WEIGHTS_GB):.1f} GB, and even the shortest context on "
                     f"the ladder at the cheapest format leaves no room beside the expert cache — "
                     f"the engine stops with 'no VRAM is left for the expert cache' "
                     f"(setup.py:5095-5097, :5139-5147), short by {-room:.1f} GB at the measured "
                     f"{KV_BYTES_PER_TOKEN_INT8:,} bytes/token{measured}.")
    if low_ram:
        parts.append(f"Its experts do not fit that RAM with the {LOW_RAM_HEADROOM_GB} GB upstream "
                     f"keeps beside them, so the installer writes a "
                     f"{tier['arena_gb'] + 1:.1f} GB expert arena to disk (the low-RAM mode).")
    if ceiling == "vram" and meets_share:
        parts.append(f"VRAM is the ceiling: {context} is the longest context whose KV cache fits "
                     f"beside the expert cache, and {relief} is what buys more.")
    elif ceiling == "vram":
        parts.append(f"VRAM is the ceiling, and a hard one: no rung of the ladder leaves this card "
                     f"the {EXPERT_CACHE_MIN_SHARE:.0%} of the expert arena this tool sizes "
                     f"against, so {relief} is what this card allows rather than a tuning choice" +
                     ("; below it the engine stops with 'no VRAM is left for the expert cache'"
                      if not runs_on_card else "") + ".")
    elif ceiling == "ram":
        parts.append(f"Host RAM is the ceiling: {_beside_model(ram, tier)}, under the "
                     f"{AGENT_CACHE_FREE_GB} GB upstream treats as room for a conversation cache "
                     f"(setup.py:5210), so the {sessions}-session cache has to come down with "
                     f"{relief}.")
    elif ram - tier["ram_gb"] < AGENT_CACHE_FREE_GB:
        parts.append(f"Host RAM is tight the same way - {_beside_model(ram, tier)}, under the "
                     f"{AGENT_CACHE_FREE_GB} GB upstream treats as room for a conversation cache - "
                     f"but metal/setup_mac.py has no --conversation-cache flag to shrink it, so this "
                     f"tool reports no ceiling it cannot relieve (spec §9); the {sessions}-session "
                     f"cache is the Mac installer's to size.")
    else:
        parts.append(f"Neither ceiling binds: {context} at --kv {kv} fits the VRAM and "
                     f"{ram - tier['ram_gb']:.1f} GiB is left beside the model for the "
                     f"{sessions}-session cache.")
    if disk_gb is not None:
        note = ""
        if tier["abliterated"]:
            note = (f" — the real size of its two shards, not upstream's "
                    f"{tier['download_gb']} GB figure for the censored file")
        if low_ram:
            note += f", plus the {tier['arena_gb'] + 1:.1f} GB expert arena the low-RAM mode writes"
        if box.get("has_shared_shard2") and tier["abliterated"]:
            note += f", less the {catalog.shared_shard2()[0] / GB:.1f} GB shard 2 it already holds"
        parts.append(f"{disk_gb} GB has to be free to install it{note}.")
    return " ".join(parts)


def _plan(box: dict, name: str, sessions: int, want_abliterated: bool) -> dict:
    """One answer, in the shape Task 7 prints."""
    tier = catalog.TIERS[name]
    ram = _ram_gib(box.get("ram_mib", 0))
    vram = _vram_gib(box.get("vram_mib", 0))
    context, kv, meets_share, runs_on_card = _kv_plan(vram, tier)
    low_ram = _low_ram_applies(ram, tier)
    disk_gb = _disk_gb(name, low_ram, bool(box.get("has_shared_shard2")))
    ceiling, relief = _ceiling(box.get("os") or "linux", ram, tier, context, kv)
    return {"tier": name, "abliterated": tier["abliterated"],
            "reason": _reason(box, name, context, kv, meets_share, runs_on_card, low_ram, disk_gb,
                              ceiling, relief, sessions, want_abliterated),
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

    ``tier`` is None on a stop or when the box is under every published RAM floor by more than
    upstream's 8 GiB tight band; ``reason`` then says which floor it was under. A tier inside that
    band is recommended and called tight in ``reason`` — upstream's own verdict for it is "tight",
    not "does not fit" (setup.py:6092, counted as fitting at :6111), and a setup tool that turns
    away a machine upstream installs is the worse error. ``ceiling`` is ``"vram"``, ``"ram"`` or
    ``"none"`` and ``relief`` names the flag the installer for that OS can set to relieve it, or
    ``""`` when nothing binds. ``alternatives`` holds the same shape, ranked, with its own
    ``alternatives`` empty — the ranking lives on the top answer.
    """
    stop = _stop_reason(box)
    if stop:
        return _no_tier(stop)
    os_name = box.get("os") or "linux"
    names = catalog.tiers_for(os_name)
    ram = _ram_gib(box["ram_mib"])
    fitting = [n for n in names if ram >= catalog.TIERS[n]["ram_gb"] - TIGHT_BAND_GB]
    if not fitting:
        return _no_tier(_floor_reason(ram, names))
    ranked = sorted(fitting, key=lambda n: _rank_key(n, want_abliterated), reverse=True)
    plans = [_plan(box, n, sessions, want_abliterated) for n in ranked]
    best, alternatives = plans[0], plans[1:]
    best["alternatives"] = alternatives
    return best
