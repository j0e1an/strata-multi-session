"""lib/vision.py: the block the config carries, and the prose that justifies the cap.

Nothing here runs a subprocess, opens a socket or writes a file. ``block()`` and ``explain()`` are
pure, and the two tests that check *where* the text lives read the repo read-only — so this module
is hermetic by construction on a box where 8080, 8090 and 18080 are all listening.

What the pins are for, in the order they matter:

* **The block shape is the written config.** ``bootstrap.sh`` dumps ``block()`` straight into the
  JSON, so the key set and the key order are the file's. ``test_the_linux_block_is_the_four_keys_then_the_cap``
  pins the order, which is the part a refactor silently reshuffles while every value stays right.
  ``tests/test_setup.py`` pins the same shape end-to-end through ``--apply``.
* **The carry-over caveat travels with the encode curve.** spec §14 lists the curve among the four
  figures measured on qwen-family GSQ-RCO IQ3_S at 262144 with ``--kv int8``, so quoting it on any
  other tier means saying so beside it. Task 7 fixed exactly this finding once; these tests are
  what stop the extraction from re-introducing it in the new place — including the direction nobody
  thinks to check, a caller that names no tier at all.
* **A carried figure names the box it came from.** The curve is a reference-box measurement, so the
  printed line says which box and which date; and every line ``explain`` returns, the label
  included, has to fit the 94-column fill, because a line that does not gets broken by the terminal
  instead of by this file.
* **The Mac note says what the Mac installer does.** ``setup_mac.py`` hardcodes ``gpu: True`` *and*
  ``max_tokens: 1024`` and offers no flag for either, so both values are pinned in the note and the
  claim that it has "no token cap" is pinned absent.
* **The curve is a Linux measurement.** spec §10 forbids quoting it on a Mac, so ``explain`` has two
  bodies and both directions are pinned.
* **One copy, not two.** The extraction only counts as an extraction while the entry point holds no
  second copy of the table, so two tests read ``bootstrap.sh`` as text. ``tests/test_setup.py``
  already scans that file for the same kind of claim (its ``PER_SESSION_PARKS`` pin), so the
  technique is the one this repo uses — and the same scan pins the cap in ``--help``, the one place
  the shell still has to type it because the help runs before the plan exists.
"""
import os
import re
import unittest

from lib import recommend, vision

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOTSTRAP = os.path.join(ROOT, "bootstrap.sh")

# A tier inside recommend.MEASURED_FAMILY and one outside it — the two directions spec §14 splits.
MEASURED_TIER = "IQ3_S"
CARRIED_TIER = "UD-Q6_K_XL"

ARGS = dict(exe="/e/strata-vision", mmproj="/m/mmproj.gguf", model="/s/shard1.gguf")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def unfolded(text):
    """The block with its wrapping undone — a sentence here can break across two lines."""
    return " ".join(text.split())


class TestVision(unittest.TestCase):
    def test_linux_default_is_cpu_at_1024(self):
        v = vision.block(**dict(ARGS, os_name="linux"))
        self.assertFalse(v["gpu"])
        self.assertEqual(v["max_tokens"], 1024)

    def test_mac_keeps_the_gpu_encoder(self):
        v = vision.block(**dict(ARGS, os_name="darwin"))
        self.assertTrue(v["gpu"])
        # setup_mac.py:677-678 hardcodes gpu: True *and* "max_tokens": 1024 in the block it writes,
        # and the Mac's live config carries both. The brief's assertNotIn("max_tokens", v) rested on
        # its own test comment ("no token cap"), not on spec §10 — upstream's source says otherwise,
        # so the cap is pinned present. 1024 is a literal here on purpose: it is upstream's number,
        # and if our Linux cap ever moves this has to fail and force the choice in the open.
        self.assertEqual(v["max_tokens"], 1024)

    def test_cpu_encode_curve_is_not_quoted_on_a_mac(self):
        self.assertNotIn("148", vision.explain("darwin"))
        self.assertIn("148", vision.explain("linux"))

    # ------------------------------------------------------------------ the block, key by key

    def test_the_linux_block_is_the_four_keys_then_the_cap(self):
        # Order is part of the contract: the apply path json.dumps()es this dict as-is, and the
        # config on disk today has the cap last.
        self.assertEqual(list(vision.block(**dict(ARGS, os_name="linux"))),
                         ["exe", "mmproj", "model", "gpu", "max_tokens"])
        # The same five keys in the same order on a Mac: that is the order setup_mac.py:677-678
        # writes them in, which is the whole reason the darwin branch exists.
        self.assertEqual(list(vision.block(**dict(ARGS, os_name="darwin"))),
                         ["exe", "mmproj", "model", "gpu", "max_tokens"])

    def test_the_paths_go_in_as_given(self):
        # block() composes nothing: the caller owns the paths, this file owns the shape.
        self.assertEqual(vision.block(**dict(ARGS, os_name="linux")),
                         {"exe": "/e/strata-vision", "mmproj": "/m/mmproj.gguf",
                          "model": "/s/shard1.gguf", "gpu": False, "max_tokens": 1024})

    def test_gpu_on_linux_keeps_the_cap(self):
        # --vision gpu changes the encoder, not the cap: server.py reads gpu: true as "add
        # --vram-reserve-mib" (spec §10) and the token ceiling is still the Linux measurement.
        v = vision.block(**dict(ARGS, os_name="linux", gpu=True))
        self.assertTrue(v["gpu"])
        self.assertEqual(v["max_tokens"], 1024)

    def test_a_mac_stays_on_the_gpu_encoder_when_asked_off(self):
        # setup_mac.py:677 hardcodes gpu: true and the Mac build has no CPU encoder to name, so
        # --vision cpu on a Mac cannot mean gpu: false. bootstrap.sh routes --vision cpu there to
        # `--vision yes`; this is the second half of that refusal.
        self.assertTrue(vision.block(**dict(ARGS, os_name="darwin", gpu=False))["gpu"])

    def test_windows_takes_the_linux_path(self):
        # lib.detect.detect() sends anything that is not darwin down the Linux path (detect.py:
        # 157-159, :169) because setup.py, not setup_mac.py, installs there (spec §8).
        v = vision.block(**dict(ARGS, os_name="windows"))
        self.assertFalse(v["gpu"])
        self.assertEqual(v["max_tokens"], 1024)

    def test_the_cap_the_block_writes_is_the_cap_the_prose_quotes(self):
        # One constant, two consumers. If the block's cap and the sentence justifying it ever
        # disagree, the plan argues for a number it did not write.
        self.assertEqual(vision.block(**dict(ARGS, os_name="linux"))["max_tokens"],
                         vision.VISION_MAX_TOKENS)
        for pattern in (r"needs at least (\d+) image tokens", r"so (\d+) is the cap"):
            quoted = re.search(pattern, unfolded(vision.explain("linux"))).group(1)
            self.assertEqual(quoted, str(vision.VISION_MAX_TOKENS), pattern)

    # ------------------------------------------------------------------- the OS split, in mode

    def test_the_default_mode_is_cpu_on_linux_and_gpu_on_a_mac(self):
        self.assertEqual(vision.default_mode("linux"), "cpu")
        self.assertEqual(vision.default_mode("darwin"), "gpu")
        self.assertEqual(vision.default_mode("windows"), "cpu")

    # ------------------------------------------------- spec §14: the curve says where it was measured

    def test_the_curve_carries_the_caveat_off_the_measured_family(self):
        text = unfolded(vision.explain("linux", CARRIED_TIER))
        self.assertIn("not measured on this tier", text)
        # The caveat is recommend's own sentence, not a copy of it — a second literal here would
        # drift the moment lib rewords it. Compared with the wrapping undone: the sentence is one
        # paragraph with the label now, so it breaks across the fill like the rest of the prose.
        self.assertIn(unfolded(recommend.carry_over_note(CARRIED_TIER)), text)

    def test_the_curve_carries_no_caveat_on_the_measured_family(self):
        # The other half: on the family it WAS measured on, the caveat is noise, and noise nobody
        # reads stops being a warning.
        self.assertNotIn("not measured", vision.explain("linux", MEASURED_TIER))

    def test_a_caller_that_names_no_tier_still_gets_the_caveat(self):
        # The direction a caller forgets: no tier means no way to know the figure was measured on
        # it, so the honest answer is to label it.
        self.assertIn("not measured on this tier", " ".join(vision.explain("linux").split()))

    def test_the_mac_note_needs_no_caveat_and_carries_no_carry_over_figure(self):
        # Nothing quoted on a Mac was measured elsewhere: the sub-second claim is a Mac measurement.
        text = vision.explain("darwin", CARRIED_TIER)
        self.assertNotIn("not measured on this tier", text)
        self.assertIn("setup_mac.py", text)
        self.assertIn(":677", text)            # the citation, not just the file name
        # Both of the installer's hardcoded values, named. setup_mac.py:677-678 writes gpu: True and
        # "max_tokens": 1024, and offers no flag for either (--vision is choices yes|no at :506, and
        # there is no --vision-tokens) — so the note that told users "no token cap" was false, and a
        # rewording that drops either value has to fail here rather than re-earn the same review.
        self.assertIn("gpu: true", text)
        self.assertIn("max_tokens: 1024", text)
        self.assertNotIn("no token cap", text)

    def test_the_mac_note_names_both_flags_the_installer_lacks(self):
        # The two things a Mac reader can actually act on: there is no --vision cpu to pass, and no
        # --vision-tokens to raise the cap with. Pinned as the strings a reader would grep for.
        text = unfolded(vision.explain("darwin", CARRIED_TIER))
        self.assertIn("--vision yes|no", text)
        self.assertIn("--vision cpu", text)
        self.assertIn("--vision-tokens", text)

    def test_the_linux_prose_keeps_its_source(self):
        # "1024 is the cap" is only an argument because mtmd says grounding needs that many tokens.
        self.assertIn("mtmd", vision.explain("linux", MEASURED_TIER))

    # ------------------------------------------------------------------- the table itself

    def test_every_measured_row_is_printed(self):
        text = vision.explain("linux", MEASURED_TIER)
        for tokens, seconds in vision.ENCODE_CURVE:
            self.assertIn("%d image tokens -> %s" % (tokens, seconds), text, str(tokens))
        self.assertEqual(len(vision.ENCODE_CURVE), 5)     # spec §10's five rows, none dropped

    def test_the_curve_is_labelled_as_a_measurement(self):
        # The line above the numbers has to say what box, what thread count and what date produced
        # them. spec §10's "this box" meant the reference box to whoever ran it; printed verbatim on
        # someone else's machine it credits that machine with a curve it never measured, so the box
        # and the date are pinned in the line the user reads, not only in the module docstring.
        folded = " ".join(vision.explain("linux", MEASURED_TIER).split())
        self.assertIn("measured on the reference box (2x5090, 8 threads, 2026-10-17):", folded)
        self.assertNotIn("measured on this box", folded)

    def test_the_prose_fits_the_column_it_is_printed_in(self):
        # No exemption. This loop used to `continue` past "encode cost:" — "one long label line, as
        # the entry point printed it" — which left the one line that overflowed the 94-column fill
        # invisible to the assertion, and the terminal broke the §14 caveat mid-sentence at whatever
        # width it happened to be. The label is a paragraph like any other now, so it is checked.
        text = vision.explain("linux", CARRIED_TIER, indent=" " * 16)
        lines = text.splitlines()
        labelled = [line for line in lines if "encode cost:" in line]
        self.assertEqual(len(labelled), 1, "the label line appears exactly once")
        self.assertLessEqual(len(labelled[0]), 94,
                             "the label line itself, not exempted: %r" % labelled[0])
        for line in lines:
            self.assertTrue(line.startswith(" " * 16), repr(line))
            self.assertLessEqual(len(line), 94, repr(line))

    def test_the_caveat_wraps_off_the_label_instead_of_riding_it(self):
        # The label alone fits the column; recommend.NOT_MEASURED_FIGURE does not fit beside it, so
        # spec §14's sentence has to continue on the lines below. If it ever rides the label again
        # the label is one ~190-column line again, and this fails beside the test above.
        lines = vision.explain("linux", CARRIED_TIER, indent=" " * 16).splitlines()
        self.assertTrue(lines[0].startswith(" " * 16 + "encode cost:"), repr(lines[0]))
        self.assertIn("not measured on this tier", "\n".join(lines[1:]))

    # ------------------------------------------------------------- one copy, in one file

    def test_the_entry_point_holds_no_second_copy_of_the_encode_curve(self):
        src = read(BOOTSTRAP)
        for _tokens, seconds in vision.ENCODE_CURVE:
            self.assertNotIn(seconds, src,
                             "%s is back in bootstrap.sh: the curve has to live in lib/vision.py"
                             % seconds)

    def test_the_entry_point_calls_both(self):
        src = read(BOOTSTRAP)
        self.assertIn("vision.explain(", src, "the vision stage stopped using lib/vision.explain")
        self.assertIn("vision.block(", src, "the config writer stopped using lib/vision.block")

    def test_the_installer_flags_take_the_cap_from_lib_not_from_a_literal(self):
        # --vision-tokens, the stage line and the written config all quote the same number, so the
        # shell has to read it out of the derived block instead of hardcoding it.
        src = read(BOOTSTRAP)
        self.assertNotIn("--vision-tokens 1024", src)
        self.assertIn('--vision-tokens "$VISION_TOKENS"', src)
        self.assertIn('("vision_tokens", vision.VISION_MAX_TOKENS)', src)

    def test_the_help_text_quotes_the_cap_lib_actually_writes(self):
        """--help is the one place the cap still has to be typed, so it is pinned to the constant.

        usage() prints a quoted heredoc: every other consumer reads VISION_TOKENS back from the
        derived block, but the help text runs before the plan does, so there is nothing there to
        read from. Deriving it would mean either an unquoted heredoc — which expands the
        $STRATA_DATA and friends printed two lines above it — or a sed of lib/vision.py at help
        time, which fails silently to "cpu at  image tokens" the moment the constant line is
        reformatted. Pinning the sentence to vision.VISION_MAX_TOKENS gets the same guarantee
        without either: move the cap and --help fails until the sentence is rewritten to match.
        """
        self.assertIn("Linux defaults to cpu at %d image tokens" % vision.VISION_MAX_TOKENS,
                      unfolded(read(BOOTSTRAP)))


if __name__ == "__main__":
    unittest.main()
