"""Vision: the encoder block the config carries, and the prose that explains it (spec §10).

Two things live here because both were being written twice. The ``vision`` section
``serve/server.py`` reads is built here (:func:`block`), and the measured encode curve the plan
shows before it asks anyone to accept a token cap is printed here (:func:`explain`). The entry
point used to hold both inline, in a preview section and again in the config writer, so the curve
had one copy in the file and the block shape had one — and a change to either had to be found in
two places. Now the entry point calls this module and owns neither.

Why the two OSes disagree, and where each figure came from
    **Linux: the CPU encoder, capped at 1024 image tokens.** spec §10 makes the CPU encoder the
    default here and the 1024 cap a measured tradeoff, not a preference. The encoder costs text
    generation nothing measurable on the reference box, so the cap is the only thing worth
    trading — and the curve below is why it sits at 1024 rather than higher.

    **macOS: there is no CPU encoder to choose.** The Mac's ``strata-vision`` is built with ggml's
    Metal backend and encodes sub-second (measured on the Mac, which is why that sentence needs no
    carry-over caveat), and ``metal/setup_mac.py`` offers only ``--vision yes|no`` while
    hardcoding ``gpu: true`` in the block it writes (:677). So :func:`block` forces the encoder on
    for darwin whatever was asked, and writes no ``max_tokens`` there: the cap is the Linux
    measurement and that installer has no token cap to write.

    **The curve is a Linux measurement, quoted on Linux only.** spec §10 says the tool must not
    quote it on a Mac and must not promise a CPU encoder there, so :func:`explain` returns the
    Metal note on darwin and the table on every other OS — and the table's numbers never reach a
    Mac screen. ``tests/test_vision.py`` pins both directions.

    **The caveat travels with the table.** spec §14 lists the CPU encode curve among the four
    figures measured on qwen-family GSQ-RCO IQ3_S at 262144 with ``--kv int8``, so carrying it
    onto another tier means saying so beside it. :func:`explain` takes the chosen tier and appends
    :func:`lib.recommend.carry_over_note` itself rather than trusting the caller to remember:
    the entry point has one screen and four of these figures, which is four chances to forget.
    A caller that names no tier gets the caveat anyway — an unlabelled measurement is the one
    failure direction this file refuses.

Windows is not a third case: it goes down the Linux path here exactly as
:func:`lib.detect.detect` routes it (detect.py:157-159, :169), because upstream's ``setup.py`` —
not ``metal/setup_mac.py`` — is what installs on Windows (spec §8).
"""

import textwrap

from lib import recommend

# spec §10's cap, and the value upstream's `--vision-tokens` takes beside it. One constant on
# purpose: the plan, the installer flags and the written config all have to agree on it, and the
# sentence in :func:`explain` that justifies it quotes the same number.
VISION_MAX_TOKENS = 1024

# spec §10's table, verbatim: image tokens -> wall-clock seconds for one CPU encode on 8 threads,
# measured 2026-10-17 on the reference box (the 2x5090 every number in this repo comes from). The
# last row is the uncapped run. The seconds are strings, not floats, because that is the precision
# they were measured at — reformatting 5.9 through "%.2f" would print a decimal place nobody
# observed, and the plan quotes these readings rather than computing from them.
ENCODE_CURVE = ((300, "1.29 s"), (768, "5.9 s"), (972, "8.7 s"), (2028, "31.8 s"),
                (4015, "148 s"))

# The plan prints this block in a prose column indented 16 and filled to 94, so the wrap happens
# here at 78 and the caller's indent brings it back to 94. Keeping the fill in this file is what
# lets the table rows and the paragraph keep their relative indentation whatever the caller does
# with the whole block.
PROSE_WIDTH = 78

# spec §10's Mac note, and the reason explain() has two bodies.
_MAC_NOTE = ("the CPU encode curve is a Linux measurement and does not apply: the Mac's "
             "strata-vision is built with ggml's Metal backend and encodes sub-second, and "
             "setup_mac.py offers only --vision yes|no while hardcoding gpu: true (:677). No "
             "--vision cpu, no token cap, on this installer.")

_LINUX_CURVE_NOTE = ("superlinear — 2.56x the tokens cost 4.6x the time, so a linear guess from "
                     "\"3 s at 300 tokens\" is wrong. mtmd warns that Qwen-VL needs at least %d "
                     "image tokens for grounding, so %d is the cap this tool writes."
                     % (VISION_MAX_TOKENS, VISION_MAX_TOKENS))


def default_mode(os_name: str) -> str:
    """The vision mode this OS gets when ``--vision`` is absent — ``cpu`` on Linux, ``gpu`` on a Mac.

    Not a preference: the Mac build has no CPU encoder to select (spec §10), so ``cpu`` there
    would name a mode the binary cannot run. Everything else — Linux and Windows — defaults to the
    CPU encoder, which is what the 1024 cap exists for.
    """
    return "gpu" if os_name == "darwin" else "cpu"


def block(exe: str, mmproj: str, model: str, os_name: str, gpu: bool = False) -> dict:
    """The ``vision`` section ``serve/server.py`` reads, in the shape the OS's installer writes.

    Key order is part of the contract, not an accident: the apply path dumps this dict straight
    into the config file, and today's written config is ``exe, mmproj, model, gpu`` then
    ``max_tokens`` on the non-darwin path only. ``tests/test_vision.py`` pins the order so a
    refactor cannot reshuffle the file.

    ``gpu`` is what was asked for — ``--vision gpu`` on Linux. It is forced on for macOS whatever
    was asked, because ``metal/setup_mac.py:677`` hardcodes ``gpu: true`` and the Mac build has no
    CPU encoder to name: a ``gpu: false`` there would be a promise the binary cannot keep.

    ``max_tokens`` is written on every non-darwin OS and on no darwin one. The cap is the Linux
    measurement in :data:`ENCODE_CURVE`, and ``setup_mac.py`` has no token cap to write (spec §10).
    ``server.py`` reads ``gpu: true`` as "add ``--vram-reserve-mib`` to the engine args"
    (spec §10), which is the one behavioural difference between the two values.
    """
    section = {"exe": exe, "mmproj": mmproj, "model": model,
               "gpu": bool(gpu) or os_name == "darwin"}
    if os_name != "darwin":
        section["max_tokens"] = VISION_MAX_TOKENS
    return section


def explain(os_name: str, tier: str | None = None, indent: str = "") -> str:
    """The prose that justifies the cap — the measured encode curve on Linux, the Metal note on a Mac.

    ``tier`` is the tier the plan chose. spec §14 requires the "not measured on this tier" caveat
    beside the curve whenever that tier is outside the family the curve was measured on, and this
    function appends :func:`lib.recommend.carry_over_note` itself rather than leaving it to the
    caller. ``tier=None`` — a caller that did not name one — gets the caveat too, because the
    honest default for an unlabelled measurement is to label it.

    ``indent`` is the caller's prose column. Lines come back wrapped to ``len(indent) +``
    :data:`PROSE_WIDTH`, so the entry point's 16-space column still fills to 94.

    The curve is a Linux measurement (spec §10), so on darwin it is not quoted at all and no
    caveat is needed: nothing measured on another tier is being carried onto this one.
    """
    if os_name == "darwin":
        return _wrap(_MAC_NOTE, indent)
    caveat = recommend.NOT_MEASURED_FIGURE if tier is None else recommend.carry_over_note(tier)
    lines = ["encode cost: measured on this box, 8 threads:%s" % caveat]
    lines += ["%5d image tokens -> %s" % (tokens, seconds) for tokens, seconds in ENCODE_CURVE]
    lines.append(_LINUX_CURVE_NOTE)
    return "\n".join(_wrap(line, indent) if i == len(lines) - 1
                     else "%s%s" % (indent, line) for i, line in enumerate(lines))


def _wrap(text: str, indent: str) -> str:
    """One paragraph, filled to the caller's prose column."""
    return textwrap.fill(text, width=len(indent) + PROSE_WIDTH, initial_indent=indent,
                         subsequent_indent=indent)
