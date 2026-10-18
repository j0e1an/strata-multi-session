"""The tier catalog: the one place a tier's numbers live.

Nothing is fetched at import time — the table ships hardcoded so the tool can answer
questions with no network at all (spec §16). Every number is copied from a pinned
source, named beside the row it came from:

  download_gb / ram_gb / arena_gb / shards / family  upstream setup.py:243-309 (``MODELS``)
  shard_bytes / shard_oids, abliterated tiers        the Hub's LFS pointers, re-read from the
                                                     tree API for SC117/Qwen3.8-Flash-Next-GSQ-
                                                     RCO-abliterated-GGUF on 2026-10-17 (spec §16)
  shard_files, abliterated tiers                     the same tree API, re-read 2026-10-18: the
                                                     eight names under ``<TIER>/`` there, each one
                                                     confirmed against the size and oid already
                                                     pinned in its own row. IQ3_S's two are the
                                                     names fetch-abliterated.sh always hardcoded
  ABLITERATED_REPO                                   the single repo those eight files live in,
                                                     also read off the old fetch script
  shard_bytes / shard_oids, Unsloth tiers            setup.py:312 / :323 / :333 (UNSLOTH_SHARDS,
                                                     UNSLOTH_IQ4_XS_SHARDS, UNSLOTH_BIG_SHARDS)
  MAC_TIERS                                          metal/setup_mac.py:45 (``MAC_MODELS``); the
                                                     family and shard count it leaves implicit are
                                                     its model_shard_names() defaults (metal/
                                                     setup_mac.py:74): family qwen, 2 shards
  total_bytes                                        derived: the row's own shard_bytes summed
  MMPROJ                                             setup.py:423 (bytes + SHA-256) and setup.py:425
                                                     (the file name)
  CONTEXTS                                           setup.py:381, verbatim

Two download figures live here, and they are not the same number on the four abliterated tiers.
``download_gb`` is upstream's published figure, rounded to 0.1 GB, and it describes the file that
row's family actually ships — except for Q2_0, IQ2_XS, IQ3_XXS and IQ3_S, where setup.py is quoting
ISTA's censored file and SC117's re-quantization is 0.26-0.47 GB larger (Q2_0: 66.4 quoted,
66,821,518,304 B = 66.82 GB real). ``total_bytes`` is the sum of the row's own ``shard_bytes``, so
it is the size of the files this repo actually fetches; it is ``None`` where upstream pins no
per-file sizes.

**A disk check uses ``download_bytes()``, never ``download_gb``.** The preflight refuses a fetch
before it starts, so a figure under the truth lets a user begin a download their disk cannot hold —
and on the abliterated tiers ``download_gb`` is under the truth. ``download_gb`` stays exactly as
upstream published it because the README and the plan quote that number.

``shard_bytes``/``shard_oids`` are empty where upstream pins no per-file hashes. IQ1_M: setup.py
checks a pinned SHA-256 only for a family that carries a ``sha256`` table (setup.py:6486-6488), and
only Unsloth's does — the qwen and Coder files are checked structurally by ``check_shards()``
(setup.py:1941) instead, so there is no oid to copy. NVFP4: a ready-made Strata pack of
``experts.bin``/``dense.bin``/PLE/embedding files, not GGUF shards — hence ``shards: 0``.

Rows are in the order the two installers' menus are worth walking: the four abliterated tiers
first (the ones this repo fetches itself), then the rest by download size. ``tiers_for()``
returns that order.
"""

# Upstream's context ladder, verbatim (setup.py:381). 204800 sits inside the trained 262144, so it
# needs no rope scaling and costs less KV than 262144 — the middle step for a PC that cannot hold 256K.
CONTEXTS = [8192, 32768, 65536, 131072, 204800, 262144, 393216, 524288]

# Shard 2 of all four abliterated tiers is the same file: the n-gram/PLE table, which does not depend
# on the expert quantization (spec §16). Upstream's own IQ3_S shard 2 carries the identical oid, which
# is why fetch-abliterated.sh hard-links it rather than downloading it — 28 GB saved per tier.
PLE_BYTES = 28_800_138_432
PLE_OID = "316b46f3a2dbd68c900f43136ab9449f9dcc3725dfd8c794847c204bc161e113"

# Where the eight abliterated shard files live. One repo and one URL base, not one per row: the four
# tiers differ only in the ``<TIER>/`` folder and the tier inside the file name, so repeating the repo
# four times would be four chances to mistype it. ``shard_plan()`` builds the download URLs from these.
ABLITERATED_REPO = "SC117/Qwen3.8-Flash-Next-GSQ-RCO-abliterated-GGUF"
HF_RESOLVE_BASE = "https://huggingface.co"

# What the tool offers on Linux (and Windows, which goes through the same setup.py). Sizes are
# upstream's own: download_gb is the download, ram_gb is the RAM that keeps every expert resident,
# arena_gb is the expert arena the low-RAM mode writes to disk.
TIERS = {
    # Abliterated: SC117's uncensored re-quantizations of the GSQ-RCO model. These four and only
    # these four (spec §8) — every other tier this table offers is the censored original.
    "Q2_0": {
        "family": "qwen", "download_gb": 66.4, "ram_gb": 48, "arena_gb": 34.0, "abliterated": True,
        "shards": 2,
        "shard_files": ["Qwen3.8-Flash-Next-GSQ-RCO-abliterated-Q2_0-00001-of-00002.gguf",
                        "Qwen3.8-Flash-Next-GSQ-RCO-abliterated-Q2_0-00002-of-00002.gguf"],
        "shard_bytes": [38_021_379_872, PLE_BYTES],
        "shard_oids": ["1620faf21c845524cbe38dcbbaf850b8d155c2299df05d8b7bfb43b642c4647e", PLE_OID],
    },
    "IQ2_XS": {
        "family": "qwen", "download_gb": 68.0, "ram_gb": 48, "arena_gb": 35.5, "abliterated": True,
        "shards": 2,
        "shard_files": ["Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ2_XS-00001-of-00002.gguf",
                        "Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ2_XS-00002-of-00002.gguf"],
        "shard_bytes": [39_665_015_072, PLE_BYTES],
        "shard_oids": ["4acc3ebb5050d20117bdc77fc39200e709dfe2b2367f3522c95b9112a30aff81", PLE_OID],
    },
    "IQ3_XXS": {
        "family": "qwen", "download_gb": 75.8, "ram_gb": 60, "arena_gb": 42.9, "abliterated": True,
        "shards": 2,
        "shard_files": ["Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_XXS-00001-of-00002.gguf",
                        "Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_XXS-00002-of-00002.gguf"],
        "shard_bytes": [47_342_144_896, PLE_BYTES],
        "shard_oids": ["03b11926b41a3d7f7a008882b1bc7a6330a3c9c43d3cad4598d31dc73042fdc5", PLE_OID],
    },
    "IQ3_S": {
        "family": "qwen", "download_gb": 83.6, "ram_gb": 62, "arena_gb": 50.3, "abliterated": True,
        "shards": 2,
        "shard_files": ["Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_S-00001-of-00002.gguf",
                        "Qwen3.8-Flash-Next-GSQ-RCO-abliterated-IQ3_S-00002-of-00002.gguf"],
        "shard_bytes": [55_063_448_064, PLE_BYTES],
        "shard_oids": ["40f369eca301ec6ea318dbb671b3132c26a2f0b89aa5458a7051b33215ee296c", PLE_OID],
    },
    # ISTA's Coder release: 256 of the 512 experts (the ones code, tools and vision use). The only
    # sub-48 GB option, not abliterated, and it needs setup's expert-profile-coder.bin.
    "IQ1_M": {
        "family": "coder", "download_gb": 58.4, "ram_gb": 32, "arena_gb": 23.4, "abliterated": False,
        "shards": 2, "shard_bytes": [], "shard_oids": [],
    },
    # Unsloth's UD quants and NVIDIA's NVFP4: all censored. Upstream installs these itself (this repo
    # only recommends them), so their shard values are upstream's pinned ones, copied whole.
    "UD-IQ4_XS": {
        "family": "unsloth", "download_gb": 93.7, "ram_gb": 48, "arena_gb": 59.5, "abliterated": False,
        "shards": 3,
        "shard_bytes": [10_946_624, 49_835_229_856, 43_836_407_744],
        "shard_oids": ["5ce89370720f8bf90890f439361282104c1aa1482d4013bb9a50923e758e71a4",
                       "577a38a2392b40ca2193cea502e1d92f60b8cd370675d308e0ec21885d9daaa7",
                       "d4634e6d84f0ebb0940be15c90d3790bf6464e3dea3a1cddc567dc0e83ad8833"],
    },
    "UD-Q4_K_XL": {
        "family": "unsloth", "download_gb": 111.3, "ram_gb": 48, "arena_gb": 77.0, "abliterated": False,
        "shards": 4,
        "shard_bytes": [10_946_624, 49_859_583_136, 49_376_141_504, 12_087_983_520],
        "shard_oids": ["4448186216b3af4cc558bbce2c3213f01608f8f8b2e5267a9767971dd3ec8082",
                       "3f342f1c1580473f1ee94ddd5b28206e8c07a70fa1a366f59d1d6c922919a6c9",
                       "56758f40269cad5cd9b0d3d6fbae0f40f6d5be6de49e4ab392dbe83157d9cbd3",
                       "753bda48b98ba4f1636134a90a967de1b2d3908a236c026e464777342e53510a"],
    },
    "NVFP4": {
        "family": "nvfp4", "download_gb": 128.0, "ram_gb": 80, "arena_gb": 68.0, "abliterated": False,
        "shards": 0, "shard_bytes": [], "shard_oids": [],
    },
    "UD-Q5_K_XL": {
        "family": "unsloth", "download_gb": 158.3, "ram_gb": 108, "arena_gb": 98.4, "abliterated": False,
        "shards": 6,
        "shard_bytes": [10_946_618, 682_434_912, 54_400_261_312, 49_990_245_824, 49_882_416_192,
                        3_320_101_792],
        "shard_oids": ["610922cdb1afe1094e95d7a3e86b12ee33317ebbf7320ac9344e21b6ae4bb69f",
                       "494ca4ed3dbf97bc28da88af3890b8877b9032f909812d00c0526a9ca5e91d2e",
                       "34efd79a80a1ce540a517a5d56171924b66ce1c38b04c904f17ad6d8ef17cf20",
                       "9ee6bbe462e6830864382d629101490625b1c95afa15492b7ad550c7bed2dd13",
                       "0b2437f661f584030cb5aeb8fb1bb70f6422518a6746bfcc0304167c7b6b8beb",
                       "55dd96c5ad0863b06b6393151b9ad0ac727b8d58a62e02a296ca5d5ddbfee4a3"],
    },
    "UD-Q6_K_XL": {
        "family": "unsloth", "download_gb": 169.2, "ram_gb": 118, "arena_gb": 109.2, "abliterated": False,
        "shards": 6,
        "shard_bytes": [10_946_624, 682_434_912, 54_400_261_312, 49_814_108_512, 49_419_317_344,
                        14_838_313_984],
        "shard_oids": ["8bdc6bad55b6699f46f60708829932ff4018d7ac9bd309c388534fa31753bdb7",
                       "494ca4ed3dbf97bc28da88af3890b8877b9032f909812d00c0526a9ca5e91d2e",
                       "34efd79a80a1ce540a517a5d56171924b66ce1c38b04c904f17ad6d8ef17cf20",
                       "0ea4b599880a5a52fcd9c188ba1443f646c69c96571904ddbd6a79246a01a997",
                       "9948e81ae8368144b135a7403d8c0f2cf9a6c2f9f692313865855b2832e696c9",
                       "e4ae255234f42b18012f6d6eec5bf615b57479b7e0a0233c47436a321ffeec3f"],
    },
    "Q8_0": {
        "family": "unsloth", "download_gb": 188.2, "ram_gb": 136, "arena_gb": 128.3, "abliterated": False,
        "shards": 6,
        "shard_bytes": [10_946_624, 682_434_912, 54_400_261_312, 49_446_841_216, 49_668_930_400,
                        34_015_618_784],
        "shard_oids": ["2dabcbb53ca537a7947bc7d20414fd464eeaf4d66d43021b5b2556cc87544ad2",
                       "494ca4ed3dbf97bc28da88af3890b8877b9032f909812d00c0526a9ca5e91d2e",
                       "34efd79a80a1ce540a517a5d56171924b66ce1c38b04c904f17ad6d8ef17cf20",
                       "bfa634025fabbd2658bf7694bc80b90e571699c768723f844c934c7ef06c691a",
                       "232a8f14cc0fa4262e7efe8593774b136fe40909e39c7a020342ddaa27259a97",
                       "538a93bca918064983409a41187ad4c68640f9aced6f29564da8f551bf86d7a5"],
    },
}

# total_bytes is derived, never typed: summing the row's own shard_bytes means the total cannot
# drift from the shard list the way a second hardcoded figure can. None where upstream pins no
# per-file sizes (IQ1_M, NVFP4) — no shards to add up, so no honest total.
for _row in TIERS.values():
    _row["total_bytes"] = sum(_row["shard_bytes"]) if _row["shard_bytes"] else None

# The Mac's menu, and it is genuinely a smaller one: setup.py hands every darwin run to
# metal/setup_mac.py, a different installer whose MAC_MODELS table omits IQ2_XS, the Coder IQ1_M,
# NVFP4, UD-IQ4_XS and Q8_0. Recommending one of those to a Mac sends the user to an installer that
# would refuse it (spec §8). shard1_gb is the file the GPU maps; the rest is read from the SSD.
MAC_TIERS = {
    "Q2_0": {"shard1_gb": 37.6, "family": "qwen", "shards": 2},
    "IQ3_XXS": {"shard1_gb": 47.0, "family": "qwen", "shards": 2},
    "IQ3_S": {"shard1_gb": 54.8, "family": "qwen", "shards": 2},
    "UD-Q4_K_XL": {"shard1_gb": 82.6, "family": "unsloth", "shards": 4},
    "UD-Q5_K_XL": {"shard1_gb": 103.9, "family": "unsloth", "shards": 6},
    "UD-Q6_K_XL": {"shard1_gb": 114.8, "family": "unsloth", "shards": 6},
}

# The vision encoder: one file for every Flash-Next family, NVFP4 included — setup.py:294-295 says the
# same file reads pictures for them all. setup.py:6498-6499 checks this hash only when the family pins
# one, and only the NVFP4 family does (setup.py:423). The Mac's installer hardcodes gpu: true in the
# vision block it writes (metal/setup_mac.py:677); the Linux default is the CPU encoder (spec §10).
MMPROJ = {
    "file": "mmproj-Qwen3.8-Flash-Next-BF16.gguf",
    "bytes": 907_543_008,
    "sha256": "b1a82259702816a5330d7bd7607cd9676b11780e79ff7348c21103ff3ce49bd0",
}


def tiers_for(os_name: str) -> list[str]:
    """The tiers the installer for this OS can actually put on disk, in recommendation order.

    Only macOS is different: setup.py:5706 hands a darwin run to metal/setup_mac.py, whose menu is a
    strict subset. Every other OS goes through setup.py itself, which covers Linux and Windows, so it
    gets the full table.
    """
    return list(MAC_TIERS if os_name == "darwin" else TIERS)


def abliterated_tiers() -> list[str]:
    """The tiers that exist uncensored — Q2_0, IQ2_XS, IQ3_XXS and IQ3_S, and nothing else.

    Anything outside this list is a censored model, and the tool says so rather than substituting
    quietly (spec §8).
    """
    return [t for t, tier in TIERS.items() if tier["abliterated"]]


def shared_shard2() -> tuple[int, str]:
    """(bytes, LFS oid) of the shard every abliterated tier shares: the n-gram/PLE table.

    Hard-linking it instead of downloading it saves 28.8 GB on the second and every later tier
    (spec §16).
    """
    return (PLE_BYTES, PLE_OID)


def shard_plan(tier: str) -> list[dict]:
    """Every file ``fetch-abliterated.sh`` needs for ``tier``: name, bytes, LFS oid, URL.

    The fetcher stays thin because the plan is assembled here: the shell asks for this and does
    nothing but download, link and verify what it is handed. Nothing is fetched to build it — the
    names, sizes and oids are the ones pinned in the row, so the plan is the same with the network
    down as with it up (``--refresh`` is what re-reads the Hub, and it is the fetcher's choice).

    The shard whose oid equals ``shared_shard2()[1]`` is the one the fetcher hard-links instead of
    downloading; callers identify it by that oid rather than by its position, since the identity
    that makes it linkable is that it is the same bytes everywhere.

    ``ValueError`` rather than an empty plan when there is nothing honest to return: a tier this
    catalog does not list, and a tier with no abliterated filenames — every censored tier, which
    this fetcher never handles and whose names here would be a guess.
    """
    if tier not in TIERS:
        raise ValueError("no such tier: %s" % tier)
    row = TIERS[tier]
    files = row.get("shard_files")
    if not files:
        raise ValueError("tier %s has no abliterated shard filenames in this catalog" % tier)
    return [
        {"file": name, "bytes": row["shard_bytes"][i], "oid": row["shard_oids"][i],
         "url": "%s/%s/resolve/main/%s/%s" % (HF_RESOLVE_BASE, ABLITERATED_REPO, tier, name)}
        for i, name in enumerate(files)
    ]


def download_bytes(tier: str) -> int | None:
    """Bytes a disk preflight must see free before fetching ``tier`` — or None if we cannot say.

    Use this, not ``download_gb``, before a download starts. The four abliterated tiers report
    ``total_bytes``, the real size of SC117's files: their ``download_gb`` is upstream's censored
    figure and sits 0.26-0.47 GB under what lands on disk, and a preflight built on it would wave
    through a fetch the disk cannot hold. Every other tier reports upstream's ``download_gb`` in
    bytes, which does describe the files that installer fetches (it is rounded to 0.1 GB, so it can
    sit a few tens of MB either side of that row's shard total — noise next to the headroom a
    preflight wants).

    None rather than a guess, in two cases: a tier this catalog does not list at all, and an
    abliterated tier whose shard sizes are not pinned — there the only figure on hand describes the
    wrong file, so quoting it would be worse than saying nothing.
    """
    row = TIERS.get(tier)
    if row is None:
        return None
    if row["abliterated"]:
        return row.get("total_bytes")
    return round(row["download_gb"] * 1_000_000_000)
