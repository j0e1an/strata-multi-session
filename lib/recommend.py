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
    :6418-6420), and upstream's own KV rates are byte counts over ``1e9`` too (setup.py:4022
    :4059). :func:`_disk_gb` and :func:`_kv_gb` stay decimal for that reason, and ``_reason``
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
    "fits"; it lives in the text, not in a key of its own (Task 7 prints a fixed shape). The
    ranking keeps the band ahead of the floor, which deviates from upstream's menu pre-selection
    (setup.py:2247, ``fitting = [m for m in names if ram >= MODELS[m]["ram_gb"]]``, which selects
    only among tiers that clear the floor and reports the tight ones without picking them): a 40.0
    GiB box is offered IQ2_XS (floor 48, tight) over IQ1_M, which clears its 32 GiB floor, and a
    52.0 GiB box IQ3_XXS (floor 60); preferring floor-clearing tiers first and falling back to the
    band would hand the reference box IQ2_XS rather than IQ3_S, and the reference box is the only
    machine this repo has measured — running IQ3_S with ``--resident-experts``.

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
    110,000 expected conversation tokens  THIS TOOL'S OWN assumption, not a measurement —
                                          ``ESTIMATED_CONVERSATION_TOKENS``: the length a fresh
                                          install's conversations reach. It is NOT the length the
                                          reference box's 1.86–2.22 GiB snapshots came from —
                                          110,000 tokens is 1.78 GiB at the measured rate, under
                                          that range's low end (see :func:`estimate_snapshot_mib`)
    ``--kv`` choices                      setup.py:5722
    ~5 GB of VRAM for the dense weights,
    buffers and a short context           setup.py:4017-4023 (``low_ram_gpu_gb``)
    what a card holds of the expert arena setup.py:4017-4023 (``low_ram_gpu_gb``), reproduced by
                                          :func:`_expert_cache_gb`; the engine stops with "no VRAM
                                          is left for the expert cache" when it reaches 0
                                          (setup.py:5095-5097, :5148)
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
                                          setup.py:4011-4014 that decides the low-RAM mode applies
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
``snapshot_from_metrics`` / ``slots_for_budget``, below), so here it appears in the ceiling's text
rather than in the tier choice — RAM caps how many sessions stay warm, it does not change which
tier is resident.

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

# setup.py:3999 / :4011-4014: the RAM the experts need beside them, and the test that puts the
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

# The same warning for a carried-over figure that is not a rate. spec §14 lists four measured
# figures — the KV rate, the 1.86 GiB average snapshot, "three parked conversations per session"
# and the CPU encode curve — and says the tool prints "not measured on this tier" beside any of
# them it carries onto another tier. :data:`NOT_MEASURED` says "that rate", which reads wrong
# beside a count of parked conversations or a table of encode seconds, so the general wording
# lives here rather than the rate's being stretched over three different kinds of figure.
NOT_MEASURED_FIGURE = (" (carried over: measured on qwen-family GSQ-RCO IQ3_S at 262144 with "
                       "--kv int8, not measured on this tier)")

GB = 1e9


def carry_over_note(name: str) -> str:
    """spec §14's caveat to print beside a figure carried onto tier ``name`` — ``""`` inside
    :data:`MEASURED_FAMILY`, so a caller appends it unconditionally and the qwen tiers stay clean.

    The caller does not have to know which family a tier is in to be honest about it, which is the
    whole reason this is a function and not a constant: an orchestrator that prints the KV rate,
    the parks count and the encode curve has three chances to remember the caveat and no way to
    check it got them all. Returns :data:`NOT_MEASURED_FIGURE`; where the figure *is* a rate,
    :func:`carry_over_rate_note` gives the rate's wording.
    """
    return "" if catalog.TIERS[name]["family"] in MEASURED_FAMILY else NOT_MEASURED_FIGURE


def carry_over_rate_note(name: str) -> str:
    """:func:`carry_over_note` for the 17,406 B/token rate itself, in :data:`NOT_MEASURED`'s words.

    Same test, different sentence: "that rate" is the right referent for a bytes-per-token figure
    and the wrong one for a snapshot size or a curve of encode seconds.
    """
    return "" if catalog.TIERS[name]["family"] in MEASURED_FAMILY else NOT_MEASURED


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
    too (``kv_streaming_ram_gb``, setup.py:4056-4059, and the ``longer`` term of
    ``low_ram_gpu_gb`` at :4022), and those are what the VRAM tests subtract from a GiB reading.
    """
    return context * _kv_bytes_per_token(kv) / GB


def _kv_room(vram_gib: float, tier: dict) -> float:
    """GB of VRAM the KV cache may take: the card less the dense weights, less the share of the
    expert arena this tool keeps room for. Negative when the card cannot hold both.

    The share is :data:`EXPERT_CACHE_MIN_SHARE`, this tool's own bar — see its comment. What
    upstream computes here is the other way round (the cache gets the remainder,
    ``low_ram_gpu_gb``, setup.py:4017-4023), and :func:`_expert_cache_gb` reproduces that. Like
    upstream, this subtracts a decimal-GB arena from a GiB card (:4023 does the same).
    """
    return max(0.0, vram_gib - DENSE_WEIGHTS_GB) - EXPERT_CACHE_MIN_SHARE * tier["arena_gb"]


def _expert_cache_gb(vram_gib: float, tier: dict, context: int, kv: str) -> float:
    """GB of the tier's expert arena the card holds at ``(context, kv)`` — upstream's model.

    ``low_ram_gpu_gb`` (setup.py:4017-4023): the VRAM less ~5 GB of dense weights, buffers and a
    32K context, less the KV of a longer context, capped at the arena. When this reaches 0 the
    engine stops with "no VRAM is left for the expert cache" (setup.py:5095-5097, and the ~8 GB
    card note at :5148), so it is also the test for "does this card run at all".

    Upstream models the longer-context KV at ``13 * cell_bytes`` B/token — 13,728 at int8
    (setup.py:4021, and ``kv_streaming_ram_gb`` at :4056-4059). This tool substitutes its own measured
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
    drops to 4-bit KV and a 4608 context before it gives the draft layer up (setup.py:5148,
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

    Upstream's own test (``low_ram_needed``, setup.py:4011-4014): the experts do not fit with the
    usual ``LOW_RAM_HEADROOM_GB`` beside them, so they are mapped from the pack's ``experts.bin``
    instead of copied into RAM, and the mode writes an ``arena_gb + 1`` GB arena to disk
    (setup.py:6418-6420). The RAM here is GiB — upstream's unit for this comparison, and the arena
    column is compared with it unconverted, exactly as at :4014. Unsloth's ``budget: True`` rows
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
        parts.append(f"The card is the short side before the context is: at {context} tokens it "
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
                     f"(setup.py:5095-5097, :5148), short by {-room:.1f} GB at the measured "
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


# --------------------------------------------------- the two answers a caller asks for by name
#
# ``recommend()`` answers "what should this box run". A setup tool also gets asked "then put THIS
# tier on it" (--tier), and "how much disk do I need before I start". Both used to be rebuilt
# inside bootstrap.sh out of this file's public constants, because the functions that do the work
# were private; that mirror drifted silently the moment _kv_plan or _disk_gb changed shape, and
# the tests pinned the numbers this box produced rather than the library's identity. These two are
# the public entry points the plan's architecture asks for — "Python carries the numbers because
# that is where they can be unit-tested without a GPU" — and they call the same private helpers
# ``recommend()`` does, so a forced tier and a recommended one cannot disagree.


def stock_download_bytes(name: str) -> int:
    """Bytes upstream's installer puts on disk for the **stock** (censored) build of ``name``.

    On the four abliterated tiers the row's ``download_gb`` is exactly that figure: setup.py
    quotes ISTA's censored file there (see lib/catalog's two-download-figures note), while
    ``download_bytes()`` returns SC117's larger re-quantization. So the stock download and the
    abliterated download are two different files, both landing in the target directory, and a
    preflight that counts only one of them under-reserves.

    The shared shard 2 is NOT subtracted here — that is the caller's arithmetic to make once, in
    :func:`disk_preflight`, because whether it is saved depends on which copy is already on disk.
    """
    return round(catalog.TIERS[name]["download_gb"] * GB)


def disk_preflight(box: dict, name: str, *, stage_one_downloads: bool = False,
                   stock_copy_held: bool = False) -> dict:
    """The disk figure the preflight refuses on, split into the parts the plan prints.

    ``stage_one_downloads`` says ``--apply``'s stage 1 will run upstream's installer, which is the
    only thing that puts the **stock** tier on disk. On the uncensored path that stage still runs
    (the engine comes from upstream, and its stock shards are where the hard-linkable shard 2
    comes from — bootstrap.sh says so in its own stage list), so the stock tier's bytes belong in
    the figure. Counting only the abliterated download let a fresh box be told ``ok`` at 135.2 GB
    for IQ3_S and then need 54.8 GB more, with the failure landing late inside upstream's own
    free-disk check (setup.py:6418).

    ``stock_copy_held`` says the stock tier's shards are already in the target directory, in which
    case upstream's installer finds them and downloads nothing.

    What is added is the stock tier's **non-shared** bytes (``stock_download_bytes`` less the
    28.8 GB shard 2), because the stock download is itself the copy the abliterated fetcher
    hard-links from: on a fresh box the two shard 2s are one file, so charging both would ask for
    28.8 GB that never appears. The residual this leaves, stated rather than hidden: a box that
    already holds an *abliterated* shard 2 but no stock copy gets the link saving and still pays
    for a second, stock-shaped shard 2, so the figure sits 28.8 GB under the truth there. That
    state — an abliterated tier fetched, Strata never installed — is rare, and the direction is
    the same under-reserve the preflight already errs toward for anything this catalog cannot
    size; ``need_gb`` is a floor, not a guarantee.

    ``need_gb`` is ``tier_gb + stock_gb``, one decimal, and ``None`` whenever ``tier_gb`` is (no
    honest download figure for the tier, so no honest total either). The other keys are what the
    plan prints beside it: ``low_ram``, the arena the mode writes (0.0 when it does not apply),
    ``shared_shard2_held``, and ``stage_one_stock`` — whether the stock line was added at all,
    which is how the disk section says which case it printed.
    """
    tier = catalog.TIERS[name]
    ram = _ram_gib(box.get("ram_mib", 0))
    low_ram = _low_ram_applies(ram, tier)
    held = bool(box.get("has_shared_shard2"))
    tier_gb = _disk_gb(name, low_ram, held)
    stock_gb = 0.0
    added = stage_one_downloads and not stock_copy_held and tier["abliterated"]
    if added:
        stock_gb = round((stock_download_bytes(name) - catalog.shared_shard2()[0]) / GB, 1)
    return {"need_gb": None if tier_gb is None else round(tier_gb + stock_gb, 1),
            "tier_gb": tier_gb, "stock_gb": stock_gb, "low_ram": low_ram,
            "arena_gb": round(tier["arena_gb"] + 1, 1) if low_ram else 0.0,
            "shared_shard2_held": held, "stage_one_stock": added}


def _forced_reason(box: dict, name: str, context: int, kv: str, low_ram: bool,
                   fits_in_ram: bool) -> str:
    """The verdict ``--tier`` gets, in upstream's three-state voice (setup.py:6092, :6111).

    Two things this has to get right, both of which an earlier version of the orchestrator got
    wrong in its own copy of this prose:

    spec §14. The context and KV format below the verdict come off the ladder built on the
    measured 17,406 B/token rate, so outside :data:`MEASURED_FAMILY` the sentence naming them
    carries :func:`carry_over_rate_note`.

    The low-RAM sentence is gated on ``low_ram``, not on ``fits_in_ram``. They are different
    tests: ``low_ram_fits`` (setup.py:4039-4042) asks whether the card's VRAM closes a RAM
    shortfall, and ``low_ram_needed`` (:4011-4014) asks whether the installer would actually put
    the tier in the mode — and Unsloth's ``budget: True`` rows are exempt from the second
    (:4051), reading part of their experts from the GGUF through ``--resident-budget-gib``
    whatever the RAM is. Gating on ``fits_in_ram`` printed "upstream's installer will put it in
    the low-RAM mode" beside a disk figure that had no arena in it, because
    :func:`_low_ram_applies` had correctly left the arena out.
    """
    tier = catalog.TIERS[name]
    ram, vram = _ram_gib(box.get("ram_mib", 0)), _vram_gib(box.get("vram_mib", 0))
    parts = [f"{name}: chosen with --tier, not recommended by this box. This box reports "
             f"{ram:.1f} GiB of RAM and {vram:.1f} GiB of VRAM."]
    if ram >= tier["ram_gb"]:
        parts.append(f"It clears its published floor of {tier['ram_gb']} GiB.")
    elif ram >= tier["ram_gb"] - TIGHT_BAND_GB:
        parts.append(f"it sits {tier['ram_gb'] - ram:.1f} GiB under the {tier['ram_gb']} GiB floor, "
                     f"inside the {TIGHT_BAND_GB} GiB band upstream's own verdict calls \"tight\" "
                     f"rather than \"does not fit\" (setup.py:6092).")
    elif fits_in_ram and low_ram:
        parts.append(f"it sits {tier['ram_gb'] - ram:.1f} GiB under the {tier['ram_gb']} GiB floor, "
                     "past the tight band, but upstream's low-RAM-mode test (setup.py:4039-4042) "
                     "still passes here: the card holds what it can of the "
                     f"{tier['arena_gb']} GB expert arena and the rest is read from the SSD, so "
                     "upstream's installer will put it in the low-RAM mode. That is \"not at a "
                     "published floor\", not \"this machine cannot run the model\".")
    elif fits_in_ram:
        parts.append(f"it sits {tier['ram_gb'] - ram:.1f} GiB under the {tier['ram_gb']} GiB floor, "
                     "past the tight band, and upstream's low-RAM-mode test (setup.py:4039-4042) "
                     f"still passes here on the card's {vram:.1f} GiB — but this tier's family is "
                     "exempt from the mode itself (setup.py:4051, ``budget: True``): it reads part "
                     "of its experts from the GGUF through --resident-budget-gib whatever the RAM "
                     "is, so no expert arena is written and the disk figure below is the download "
                     "alone. That is \"not at a published floor\", not \"this machine cannot run "
                     "the model\".")
    if tier["abliterated"]:
        parts.append("This is the uncensored build.")
    else:
        parts.append(f"This is the censored model: the uncensored builds exist at "
                     f"{', '.join(catalog.abliterated_tiers())} only.")
    parts.append(f"The context and KV format below are this tool's ladder against this tier's "
                 f"{tier['arena_gb']} GB expert arena: {context} tokens at --kv {kv} is the longest "
                 f"rung whose KV cache fits beside the share of the arena this tool keeps room "
                 f"for, at the measured {KV_BYTES_PER_TOKEN_INT8:,} bytes/token"
                 f"{carry_over_rate_note(name)}.")
    if low_ram:
        parts.append(f"The low-RAM mode applies here (setup.py:4011-4014), so the "
                     f"{tier['arena_gb'] + 1:.1f} GB expert arena is part of the disk figure.")
    return " ".join(parts)


def forced_plan(box: dict, name: str, sessions: int = 4, want_abliterated: bool = True) -> dict:
    """The plan for a tier the user named but :func:`recommend` did not offer.

    Same shape as :func:`recommend`'s answer — the keys Task 7 prints, with ``alternatives`` empty
    — plus ``forced`` (so the printer can say which it is), ``fits_in_ram`` (upstream's permissive
    verdict, which decides whether the "does not fit" paragraph goes on) and ``ram_gib`` (the
    figure that paragraph quotes back). ``context``, ``kv``, ``low_ram``, ``extra_disk_gb``,
    ``ceiling`` and ``relief`` come from the same private helpers :func:`recommend` uses, which is
    the point: a tier reached by ``--tier`` and the same tier reached as a recommendation cannot
    report two different disk figures for one box.

    ``extra_disk_gb`` is the tier's own figure only — the download, the arena when the mode
    applies, less the shared shard 2 when the box holds one. The stock tier's bytes that
    ``--apply``'s stage 1 adds are not in it, because whether they land is a fact about the
    install, not about the tier; :func:`disk_preflight` is what folds them in.

    Raises ``KeyError`` for a tier this catalog does not list — the caller checks the name against
    :func:`lib.catalog.tiers_for` first, and a name that got past that is a bug, not a stop.
    """
    tier = catalog.TIERS[name]
    ram = _ram_gib(box.get("ram_mib", 0))
    vram = _vram_gib(box.get("vram_mib", 0))
    context, kv, _meets_share, _runs_on_card = _kv_plan(vram, tier)
    low_ram = _low_ram_applies(ram, tier)
    fits_in_ram, _needs_mode = fits(box, tier)
    return {"tier": name, "abliterated": tier["abliterated"],
            "reason": _forced_reason(box, name, context, kv, low_ram, fits_in_ram),
            "context": context, "kv": kv, "low_ram": low_ram,
            "extra_disk_gb": _disk_gb(name, low_ram, bool(box.get("has_shared_shard2"))),
            "ceiling": _ceiling(box.get("os") or "linux", ram, tier, context, kv)[0],
            "relief": _ceiling(box.get("os") or "linux", ram, tier, context, kv)[1],
            "alternatives": [], "forced": True, "fits_in_ram": fits_in_ram, "ram_gib": ram}


# ------------------------------------------------------- first-run sizing (spec §9, phase one)
#
# Two phases, in code. Phase one — these three functions — sizes a box that has never parked a
# conversation from the measured per-token rate applied to an *expected conversation length*, and
# labels the result an estimate. Phase two is ``apply-concurrency-fix.sh`` re-run once the engine
# has parked something: its live-``/metrics`` path replaces the estimate with a measurement, and
# spec §9 is explicit that the tool has to tell the user to expect that second pass rather than
# pretend the first one is final. Task 7 prints what these return.
#
# Two of them reproduce arithmetic that already ships: Task 7 prints one source or the other, so a
# reader comparing two answers about one engine must not see two numbers, or two names for the same
# number. What tests/test_sizing.py checks against the script's own text is the label, the fallback
# figure and the two KV divergences; the arithmetic itself is pinned to literals copied from the
# script's lines, so editing the script does not re-check the arithmetic on its own.

# One MiB, spelled out because the script spells it the same way (apply-concurrency-fix.sh:191
# :206) and the two files have to land on the same integer, including where they truncate.
MIB = 1_048_576

# What a conversation is expected to reach, in tokens, on a box with no history. This tool's own
# assumption, not a measurement: at the measured rate it is 1,825 MiB = 1.78 GiB, which sits
# *between* the readings this box has produced — 1.43-1.62 GiB on 2026-10-18 and 1.86-2.22 GiB on
# 2026-10-17 (spec §9) — so phase two can come back on either side of it. See :func:`
# estimate_snapshot_mib` for the arithmetic and for what would invalidate the constant.
ESTIMATED_CONVERSATION_TOKENS = 110_000

# The script's last-resort snapshot size, for a caller with no measurement, no --snapshot-mib and
# no context to size from: ``want_snap or measured_snap_mib or theoretical_snap_mib or 2560``
# (apply-concurrency-fix.sh:208), labelled "fallback 2.5 GiB" at :212. A number in this file rather
# than a sentence about one, so Task 7 can name it without hardcoding a second copy — the drift the
# plan's "numbers live in lib/" rule exists to prevent.
FALLBACK_SNAPSHOT_MIB = 2_560

# The script's label, verbatim (apply-concurrency-fix.sh:210), for the same reason its division is
# verbatim: one engine, one name for what it measured. The other two are prefixed ``estimate:`` so
# a printed line can never be read as a measurement (Review Focus 5).
MEASURED_SNAPSHOT = "measured from /metrics"
NO_PARK_SNAPSHOT = "estimate: engine has parked nothing yet"
NO_BYTES_SNAPSHOT = "estimate: engine reports no snapshot bytes yet"


def estimate_snapshot_mib(context: int, expected_tokens: int | None = None,
                          kv: str = "int8") -> int:
    """MiB one parked snapshot costs at ``context``, sized from an expected conversation length.

    Why not ``context`` itself. 17,406 B/token x 262144 is 4,351 MiB = 4.25 GiB, against the
    1.86 GiB the reference box actually parked (spec §9) — sizing from the window is ~2.3x
    pessimistic, and on a 60 GB box that is "budget exhausted at four slots" instead of "four
    sessions fit". So the multiply is by an expected conversation length, :data:`
    ESTIMATED_CONVERSATION_TOKENS` unless the caller passes one, capped at ``context`` because a
    conversation cannot outlive the window it was generated in: a 32,768-token box sized from
    110,000 tokens would be over-provisioned 3.4x, the same class of error in the other direction.

    What 110,000 is, and what it is not. It is what a **fresh** install's conversations look like
    before they mature. It is NOT the length the reference box's 1.86-2.22 GiB snapshots came from,
    which is how an earlier draft of this function described it: at 17,406 B/token 110,000 tokens
    is 1,825 MiB = 1.78 GiB, and inverting spec §9's range at that rate gives 114,739 tokens
    (1.86 GiB) and 136,947 tokens (2.22 GiB) — so the constant is below the range measured on
    2026-10-17, on a box that had been running since the morning (1.86 GiB at 15:00, 2.22 GiB at
    21:00). It is **above** what the same box read on 2026-10-18: 1,465 MiB = 1.43 GiB across 12
    parks, and 1,663 MiB = 1.62 GiB, two probes minutes apart — 1.43 GiB is ~88,000 tokens at this
    rate and 1.62 GiB is ~100,000. The constant therefore sits *between* observed readings, not
    under them, and the phase-two reading may come back larger or smaller than this estimate;
    neither direction is the expected one, which is what "this is an estimate, re-measure it" means.

    What would invalidate it. A sustained average parked size materially **above** the 1.86 GiB low
    end on a box whose conversations are known to be short-lived: that would mean 110,000 tokens is
    too generous rather than too mean as a length — conversations that short do not reach it — while
    the 1,825 MiB it produces is still too small a size for what that box parks. The length-to-bytes
    model would be wrong, not the constant, and the fix is a measurement rather than a nudge. The
    other direction is the one today's box is in: a sustained average at or under 1.43 GiB means
    this estimate over-provisions and the slot count comes out too high. spec §9's expiry warning
    was written from readings that moved only upward (1.86 → 2.22 GiB in six hours); the 2026-10-18
    readings move the other way, so that warning is bidirectional. tests/test_sizing.py pins the
    arithmetic half (1,825 MiB, under 1,904 MiB); the direction is a judgement about a box this tool
    has not seen, and it is the part that goes stale first.

    ``kv`` scales the rate by the engine's own per-cell bytes (:data:`KV_CELL_BYTES`; 816 and 576 at
    setup.py:198, int8's 1056 at setup.py:197), the check spec §9 names. **Known divergence,
    deliberately not unified, on two formats — and the two are different kinds of disagreement.** On
    ``k8v4`` the script has **no key at all**: ``apply-concurrency-fix.sh:188-190`` lists no
    ``k8v4``, so ``KV_FACTOR.get(kv, 1.0)`` at :190 falls through to 1.0, and the same 110,000
    tokens cost 1,825 MiB there against 1,410 MiB here (816/1056 = 0.77) — a 22.7% gap, from a
    missing key. On ``q4_0`` **both files have a key and simply disagree on the number**: 576/1056 =
    0.545 here against ``"q4_0": 0.5`` at :188 there, 995 MiB against 912 MiB on the same token
    basis — a 9.1% gap, from a different number, and it runs the other way: here is the larger of
    the two (at the script's own whole-window basis, 2,373 MiB against 2,175 MiB at 262,144).
    Reviewer and Task 7: one config, two snapshot sizes, on ``k8v4`` and ``q4_0``; ``int8`` is the
    only format where the two files agree. Only :data:`KV_FORMATS` are accepted here; the script's
    wider table (q8_0, int4, q4_k, f16, bf16, f32) raises ``KeyError`` rather than quietly taking
    that 1.0 default.

    Spec §14: the rate is measured on qwen-family GSQ-RCO IQ3_S at 262144 with ``--kv int8``. The
    return is a bare int, so the labelling duty is the caller's — outside the qwen family print
    :data:`NOT_MEASURED` beside it, and never print this as a measurement of the caller's box.
    Truncates toward zero like the script's ``int(...)`` at :191, so the two never differ by a
    rounding step. Returns 0 when ``context`` is 0 — the script's own ``if max_context else 0`` at
    :191, meaning "no context configured, nothing to size", not "free". It returns 0 the same way
    when ``expected_tokens`` is 0 or negative: a conversation of no length parks nothing, so the
    answer is "nothing to size", and :func:`slots_for_budget` refuses a 0 rather than counting slots
    against it. That is a documented zero, not a stop — the caller has to label this figure as an
    estimate either way, and a zero it already handles beats an exception it has to catch.
    """
    tokens = ESTIMATED_CONVERSATION_TOKENS if expected_tokens is None else expected_tokens
    tokens = min(tokens, context)
    if tokens <= 0:
        return 0
    return int(tokens * _kv_bytes_per_token(kv) // MIB)


def snapshot_from_metrics(parked: int, total_bytes: int) -> tuple[int, str]:
    """``(mib, source_label)`` — the engine's own average parked snapshot, or a labelled zero.

    Reproduces ``apply-concurrency-fix.sh:206`` exactly — ``int(c["bytes"] / c["parked"] /
    1048576)``, truncating rather than rounding — and returns the script's label string verbatim
    (:210), because Task 7 prints one source or the other and a reader comparing two answers about
    one engine must not see two numbers, or two names for the same number.

    The label is the point of the return shape. ``"measured from /metrics"`` is a reading of the
    caller's own engine, so spec §14's carry-over warning does not apply to it and it says nothing
    about the tier — it is not a carried-over figure. Anything else returned is an estimate and is
    labelled ``"estimate: ..."`` so a caller can never mistake it for a measurement.

    **The zero is not a size, and the caller has to know it.** ``(0, "estimate: ...")`` comes back
    when the engine has parked nothing yet, or has parked something but reports no bytes — the same
    two tests the script's own guard makes at :205 (``if c.get("parked") and c.get("bytes")``). The
    script then treats a zero as "fall through to the next source" (``want_snap or
    measured_snap_mib or theoretical_snap_mib or 2560``, :208) and never sizes from it, so a 0 here
    means **"no measurement exists — do not size from this"**, and the caller must substitute
    :func:`estimate_snapshot_mib` (or :data:`FALLBACK_SNAPSHOT_MIB`, that 2,560) before computing a
    budget or a slot count. Task 7 leans on this contract, and :func:`slots_for_budget` refuses a 0
    so the substitution cannot be skipped by accident.
    """
    if parked <= 0:
        return 0, NO_PARK_SNAPSHOT
    if total_bytes <= 0:
        return 0, NO_BYTES_SNAPSHOT
    return int(total_bytes / parked / MIB), MEASURED_SNAPSHOT


def slots_for_budget(budget_mib: int, snapshot_mib: int) -> int:
    """How many slots a ``budget_mib`` budget holds at ``snapshot_mib`` each — ``ceil``, so the
    budget and the slot count agree by construction.

    The question this answers is "how many conversations can this budget warm". It is **not** the
    script's budget computation: ``apply-concurrency-fix.sh:241-243`` sets ``per_session = 3``
    parked conversations per live session, observed on the reference box, and :251 turns it into the
    budget the other way round — ``need_mib = sessions * per_session * snapshot_mib`` — with no
    slot count anywhere in it. Folding that 3 in here would answer the script's question instead of
    this one (and report 5 slots where 14 fit), so this function takes two arguments and multiplies
    by nothing.

    Rounds up, matching the script at :272 (``max(1, -(-budget_mib // snapshot_mib))``): a floor
    leaves ``budget_mib - slots * snapshot_mib`` as budget that can never park anything, the dead
    capacity the script's own comment at :269-271 calls out. At 30,720 MiB and 2,272 MiB that is 14
    slots, not 13 — 13 leave 1,184 MiB idle. The ``max(1, ...)`` floor is the script's too: a budget
    under one snapshot still gets one slot, and a **negative** budget is that case taken past its
    meaning. A negative budget is not a meaningful input here — the script's own budget comes from
    ``min(need_mib, ram_allowance_mib)`` at :255, and ``ram_allowance_mib`` is floored at 0 at :254,
    so it cannot go negative — and this reproduces the script's floor rather than validating the
    caller: -5,000 answers 1, the same as 0. Documented rather than a stop, because the line it
    mirrors answers 1 and a stop here would make the two files disagree about the same input.

    ``snapshot_mib`` 0 raises instead of answering. 0 is what :func:`snapshot_from_metrics` returns
    when no measurement exists, and dividing by it would print a slot count with no size behind it
    — the guess spec §12 forbids. Substitute :func:`estimate_snapshot_mib` first.
    """
    if snapshot_mib <= 0:
        raise ValueError("slots_for_budget: snapshot_mib is 0, which means no snapshot size is "
                         "known — size it with estimate_snapshot_mib() (or the engine's own "
                         "measurement) before counting slots")
    return max(1, -(-budget_mib // snapshot_mib))


# ------------------------------------------------------------------ the budget the flags carry
#
# ``slots_for_budget`` answers "how many slots does a budget hold". It does not answer "what
# budget should this box get", which is the question a setup tool has to answer before the config
# exists at all. apply-concurrency-fix.sh:241-255 does, and bootstrap.sh reproduced it line for
# line — a copy that drifted silently the moment the script's shape changed, which is the drift
# the plan's "numbers live in lib/" rule exists to prevent. These are the two measured constants
# that copy was holding, named here so the entry point can print them without owning them.

# One live session parks MORE than one conversation over time: four sessions produced 99 parks
# against 12 resident slots on the reference box (apply-concurrency-fix.sh:236-241, which keeps
# the same 3 as its default at :241). Sizing the budget as sessions x ONE snapshot under-provisions
# it badly — that is what made an earlier revision of that script shrink a box that was already
# evicting. spec §14 lists "three parked conversations per session" among the figures measured on
# qwen-family GSQ-RCO IQ3_S at 262144, so a caller printing it for another tier carries
# :func:`carry_over_note` beside it.
PER_SESSION_PARKS = 3

# apply-concurrency-fix.sh:253-254: the RAM the script leaves out of the budget's way — the guard
# floor plus a working margin, never less than 6,400 MiB and never less than 11 % of MemTotal.
# Both figures are the script's, read off its lines rather than reinvented here.
RAM_RESERVE_FLOOR_MIB = 6_400
RAM_RESERVE_SHARE = 0.11


def phase_one_budget(ram_mib: int, sessions: int, snapshot_mib: int) -> dict:
    """``{budget_mib, slots, capped, reserve_mib, allowance_mib, snapshot_mib, fallback}``.

    The budget phase one writes into a fresh config: ``sessions x PER_SESSION_PARKS x
    snapshot_mib``, capped by what the RAM can actually back, rounded up to a whole GiB, with the
    slot count that agrees with it. Reproduces apply-concurrency-fix.sh:241-255 and :266 — the
    ``max(6400, 11 %)`` reserve at :253, the cap at :254-255, the whole-GiB round-up at :266 — and
    takes the slot count from :func:`slots_for_budget`, which is already pinned to the script's
    ``ceil`` at :272.

    A ``snapshot_mib`` of 0 means no size is known, and :func:`slots_for_budget` refuses it rather
    than counting slots against nothing. That is the documented zero of
    :func:`estimate_snapshot_mib` arriving here, so the figure is replaced with
    :data:`FALLBACK_SNAPSHOT_MIB` — the script's own last resort at :208 — and ``fallback`` says
    so, because a caller that labelled the number an estimate has to keep labelling it one when it
    becomes the fallback instead. The substitution happens here rather than in the caller so the
    budget and the slot count can never be sized from two different snapshots.

    ``capped`` is why the plan says "capped by the RAM allowance" instead of printing a budget the
    sessions asked for: the need is above the allowance, so the box gets less than it wanted.
    """
    snapshot = snapshot_mib
    fallback = False
    if snapshot <= 0:
        snapshot, fallback = FALLBACK_SNAPSHOT_MIB, True
    reserve_mib = max(RAM_RESERVE_FLOOR_MIB, int(ram_mib * RAM_RESERVE_SHARE))
    allowance_mib = max(0, ram_mib - reserve_mib)
    need_mib = sessions * PER_SESSION_PARKS * snapshot
    budget_mib = ((min(need_mib, allowance_mib) + 1023) // 1024) * 1024
    return {"budget_mib": budget_mib, "slots": slots_for_budget(budget_mib, snapshot),
            "capped": need_mib > allowance_mib, "reserve_mib": reserve_mib,
            "allowance_mib": allowance_mib, "snapshot_mib": snapshot, "fallback": fallback}
