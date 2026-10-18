"""Auto-start: the unit, the plist, the enable/undo pair, and the router probe (spec §11).

Per-user on both OSes, so nothing here needs a password: a systemd *user* unit and a
LaunchAgent, never a system unit. The tests are written against what the two service managers
actually accept, which is not what the plan assumed:

* **No ``--open``, and no ``--no-browser`` either.** ``write_run_script()`` (upstream
  ``setup.py:5388-5391``) appends ``--open`` unless ``--no-browser`` was passed, and inside a
  unit that means a browser on a headless box. The fix is to omit ``--open``; ``serve/server.py``
  parses strictly (``serve/server.py:6669``) and has no ``--no-browser`` of its own -- that flag
  belongs to ``setup.py:5759`` -- so a unit that passes it exits 2 on every start and ``Restart=``
  spins it. Measured on this box 2026-10-18: ``server.py: error: unrecognized arguments:
  --no-browser``, exit 2.
* **Absolute paths in the body, ``~`` only in the display path.** systemd refuses a tilde
  (``systemd-analyze verify``: "Neither a valid executable name nor an absolute path ... Unit
  configuration has fatal error, unit will not be started") and launchd does not expand one
  either, while ``unit_path()`` answers "where do I write this" and keeps the ``~`` form the
  plan pins. Task 7 hands ``render()`` paths that came from a ``~/Strata-data`` default, so the
  two answers genuinely differ.
* **The port and the interpreter are arguments.** ``server.py`` defaults to 8095
  (``serve/server.py:6634``) and reads no port from the config -- ``:6678`` takes ``a.port``
  from the flag alone -- while this box's config says ``port: 18080``. Dropping ``--port``
  starts the server somewhere nothing else in the install expects. The interpreter is an
  argument for the same reason: upstream's launcher uses ``sys.executable``, the venv, and
  ``detect.detect()`` already returns ``venv_python`` for this handoff.
* **Linux logs to the journal, and the enable line runs its steps independently.** No
  ``StandardOutput=`` in the unit: spec §11 says Linux logs through ``journalctl --user -u
  strata``, and a redirect to a file both empties the journal and fails the start when the
  directory is missing. In ``enable_commands`` the ``loginctl`` step is separated by ``;`` rather
  than ``&&``, because ``systemctl --user enable --now`` exits 1 when the *start* job fails and a
  gated linger step is skipped exactly when it is needed. Two tests execute those lines against
  stub managers on ``PATH`` -- the only way to pin which steps really run.

The module renders strings and returns commands; it never writes a file and never runs
``systemctl``/``launchctl`` (Task 7 decides whether to run what it gets back). Two tests guard
that shape rather than a value: one asserts ``render()`` leaves the filesystem alone, one runs
``systemd-analyze verify`` over the rendered unit -- the only way the tilde rule and the
quoting rule are checked by something other than my own reading.
"""

import inspect
import os
import plistlib
import shlex
import shutil
import socket
import subprocess
import tempfile
import unittest

from lib import autostart

SERVER = "/home/strata/serve/server.py"
CONFIG = "/home/strata/strata-iq3_s-abliterated.json"
LOG = "/home/strata-data/logs/strata.log"
VENV = "/home/strata/.venv/bin/python"

# The argv both OSes must run: upstream's own launcher line (setup.py:5390) minus --open.
ARGV = [VENV, SERVER, "--engine", "strata", "--config", CONFIG, "--port", "18080"]


def exec_start(unit: str) -> list[str]:
    """The unit's ExecStart argv, shell-split. Absolute paths only, so shlex is enough."""
    lines = [ln for ln in unit.splitlines() if ln.startswith("ExecStart=")]
    assert len(lines) == 1, lines
    return shlex.split(lines[0][len("ExecStart="):])


# A stand-in for a service manager: fail the enable/disable verb the way the real one does when the
# *start* job fails (measured on this box 2026-10-18 with a throwaway user unit whose ExecStart was
# /bin/false: `systemctl --user enable --now` exited 1, unit enabled, not running), and log the
# exit code it used. daemon-reload stays 0 so a `&&` chain still reaches the enable. The exit code
# is what the log carries — not a separate "this failed" marker — so a test cannot be told the
# enable failed by a stub that then exits 0.
STUB_MANAGER = """#!/bin/sh
rc=0
case " $* " in *" enable "*|*" disable "*) rc=1 ;; esac
printf '%s\\t%s\\t%s\\n' 'NAME' "$*" "$rc" >> "$STUB_LOG"
exit "$rc"
"""

# Fixed so the logged line does not move with whoever runs the suite.
STUB_USER = "strata-test-user"


def manager_calls_for(cmd: str) -> tuple[list[str], list[str], int]:
    """Run one enable/undo line through /bin/sh with fake managers on ``PATH``.

    Returns ``(calls, failed, returncode)``: every manager call in order, the subset whose stub
    exited non-zero, and the line's own exit status. This is the only way to pin "the linger step is
    not gated behind systemctl's exit status" without trusting my reading of the string — the real
    line is executed, the systemctl stub fails the enable the way the box does, and the log says
    whether ``loginctl`` was reached anyway. Callers assert ``failed`` is non-empty so the pin can
    never go vacuous by the stub quietly stopping failing.
    """
    with tempfile.TemporaryDirectory() as d:
        logfile = os.path.join(d, "calls")
        for name in ("systemctl", "loginctl", "launchctl"):
            script = os.path.join(d, name)
            with open(script, "w", encoding="utf-8") as f:
                f.write(STUB_MANAGER.replace("NAME", name))
            os.chmod(script, 0o755)
        env = dict(os.environ, PATH=d + os.pathsep + os.environ.get("PATH", ""),
                   STUB_LOG=logfile, USER=STUB_USER)
        proc = subprocess.run(cmd, shell=True, env=env, capture_output=True, text=True,
                              check=False)
        calls, failed = [], []
        if os.path.exists(logfile):            # no file at all: nothing in the line ran
            with open(logfile, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    name, args, rc = line.rstrip("\n").split("\t")
                    call = (name + " " + args).strip()
                    calls.append(call)
                    if rc != "0":
                        failed.append(call)
        return calls, failed, proc.returncode


class TestUnitPath(unittest.TestCase):
    def test_unit_paths_are_per_user(self):
        # spec §11, verbatim locations. Display paths: the `~` stays, because this one answers
        # "where do I write this" and Task 7 prints it to a human.
        self.assertEqual(autostart.unit_path("linux"), "~/.config/systemd/user/strata.service")
        self.assertEqual(autostart.unit_path("darwin"), "~/Library/LaunchAgents/dev.strata.plist")

    def test_an_os_this_repo_does_not_support_has_no_unit_path(self):
        # detect.detect() only ever returns linux/darwin, so this is a programming-error guard.
        with self.assertRaises(ValueError):
            autostart.unit_path("windows")

    def test_render_refuses_an_os_it_has_no_template_for(self):
        # Handing back a systemd unit for "windows" is a wrong answer printed to a user; a raise
        # is a bug the author finds on the first run instead.
        with self.assertRaises(ValueError):
            autostart.render("windows", SERVER, CONFIG, LOG)


class TestLinuxUnit(unittest.TestCase):
    def unit(self, **kw):
        return autostart.render("linux", SERVER, CONFIG, LOG, python_bin=VENV, **kw)

    def test_unit_omits_open_and_never_passes_no_browser(self):
        u = self.unit(port=18080)
        self.assertNotIn("--open", u)          # setup.py:5388 appends it; a unit has no display
        self.assertNotIn("--browser", u)       # server.py has neither --browser nor --no-browser
        self.assertNotIn("--no-browser", u)

    def test_unit_runs_the_server_directly_not_upstreams_launcher(self):
        # spec §11: the unit owns serve/server.py, and does not exec run-<model>.sh.
        u = self.unit(port=18080)
        self.assertIn("--engine strata", u)
        self.assertIn(CONFIG, u)
        self.assertNotIn(".sh", u)
        self.assertNotIn("run-", u)

    def test_unit_passes_the_port_the_install_listens_on(self):
        # server.py:6634 defaults to 8095 and the config's "port" is never read by server.py,
        # so the flag is the only thing that puts the server on the box's real 18080.
        self.assertIn("--port 18080", self.unit(port=18080))
        self.assertIn("--port 8095", self.unit())          # the honest default: server.py's own

    def test_unit_runs_the_interpreter_it_is_given(self):
        # The venv is what has the pinned dependencies; system python3 working here is luck.
        self.assertEqual(exec_start(self.unit(port=18080))[0], VENV)
        self.assertEqual(exec_start(autostart.render("linux", SERVER, CONFIG, LOG))[0], "python3")

    def test_exec_start_is_the_launcher_line(self):
        self.assertEqual(exec_start(self.unit(port=18080)), ARGV)

    def test_unit_body_holds_no_tilde(self):
        # Task 7's defaults are ~/Strata-data paths; systemd calls a tilde in ExecStart fatal.
        u = autostart.render("linux", "~/Strata/serve/server.py", "~/Strata/c.json",
                             "~/Strata-data/logs/strata.log")
        self.assertNotIn("~", u)
        self.assertIn(os.path.join(os.path.expanduser("~"), "Strata/serve/server.py"), u)

    def test_working_directory_is_the_absolute_root_of_the_server(self):
        # One level above serve/, not the directory holding server.py: server.py:57 computes its
        # ROOT as parents[1], upstream's launcher cds into that ROOT (setup.py:5398), and the
        # config's own "cwd" on this box is /home/joe/Strata (:40). server.py:6736 resolves a
        # relative "exe" against cfg.get("cwd") or ".", so for a config without that key the
        # process cwd is what the engine's paths come out of. Pinned with newlines either side so
        # `/home/strata` cannot pass as a prefix of `/home/strata/serve`.
        u = self.unit(port=18080)
        self.assertIn("\nWorkingDirectory=/home/strata\n", u)
        self.assertNotIn("WorkingDirectory=~", autostart.render(
            "linux", "~/Strata/serve/server.py", CONFIG, LOG))

    def test_the_linux_unit_logs_to_the_journal_not_a_file(self):
        # spec §11: Linux "logs through `journalctl --user -u strata`". A StandardOutput= redirect
        # moves the server's output out of the journal, and measured on this box 2026-10-18 it is a
        # start failure waiting to happen too: `append:` against a parent directory that does not
        # exist fails the start outright, `ExecStart=/bin/false (code=exited, status=209/STDOUT)`.
        # render() still takes `log` — the plist needs it — but on Linux it must not reach the body.
        u = self.unit(port=18080)
        self.assertNotIn("StandardOutput", u)
        self.assertNotIn("StandardError", u)
        self.assertNotIn("append:", u)
        self.assertNotIn(LOG, u)
        self.assertNotIn("StandardOutput", autostart.render(
            "linux", SERVER, CONFIG, "~/Strata-data/logs/strata.log"))

    def test_a_relative_server_path_is_resolved_but_a_bare_program_name_is_not(self):
        # Two halves of one rule, found by systemd-analyze verify: a relative *path* would leave
        # WorkingDirectory empty, so it is resolved against the cwd; `python3` is a $PATH lookup
        # and making it absolute produces `<cwd>/python3`, which is not executable.
        u = autostart.render("linux", "serve/server.py", CONFIG, LOG)
        self.assertIn("\nWorkingDirectory=" + os.getcwd() + "\n", u)
        self.assertEqual(exec_start(u)[0], "python3")

    def test_a_path_with_a_space_survives_systemds_quoting(self):
        # systemd splits ExecStart itself; an unquoted space becomes a second argument.
        u = autostart.render("linux", "/home/strata box/serve/server.py", CONFIG, LOG)
        self.assertIn('"/home/strata box/serve/server.py"', u)
        self.assertEqual(exec_start(u)[1], "/home/strata box/serve/server.py")

    def test_unit_needs_no_sudo_and_is_a_user_unit(self):
        u = self.unit(port=18080)
        self.assertNotIn("sudo", u)
        self.assertIn("[Install]", u)
        self.assertIn("WantedBy=default.target", u)      # user units stop at default.target

    def test_render_writes_nothing(self):
        # This module renders strings; Task 7 owns the write and the enable.
        with tempfile.TemporaryDirectory() as d:
            for os_name in ("linux", "darwin"):
                autostart.render(os_name, os.path.join(d, "server.py"), os.path.join(d, "c.json"),
                                 os.path.join(d, "strata.log"))
            self.assertEqual(os.listdir(d), [])

    @unittest.skipIf(shutil.which("systemd-analyze") is None, "no systemd-analyze on this box")
    def test_systemd_accepts_the_rendered_unit(self):
        # The only check here that is not my reading of systemd's rules: a unit that is 90%
        # right is a unit that never starts, and the tilde failure below is exactly what the
        # plan's absolute-path-only test could not see.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "strata.service")
            with open(path, "w", encoding="utf-8") as f:
                f.write(autostart.render("linux", "~/Strata/serve/server.py", "~/Strata/c.json",
                                         "~/Strata-data/logs/strata.log", port=18080))
            rc = subprocess.run(["systemd-analyze", "verify", path],
                                capture_output=True, text=True)
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)


class TestMacPlist(unittest.TestCase):
    def plist(self, **kw):
        return autostart.render("darwin", SERVER, CONFIG, LOG, python_bin=VENV, **kw)

    def test_mac_render_is_a_plist_not_a_unit(self):
        p = self.plist(port=18080)
        self.assertIn("RunAtLoad", p)
        self.assertIn("KeepAlive", p)
        self.assertNotIn("[Service]", p)
        self.assertNotIn("--open", p)

    def test_plist_parses_and_carries_what_launchd_reads(self):
        # A plist that is 90% right is a plist launchd rejects, so parse it, don't substring it.
        d = plistlib.loads(self.plist(port=18080).encode("utf-8"))
        self.assertEqual(d["Label"], "dev.strata")
        self.assertEqual(d["ProgramArguments"], ARGV)
        self.assertIs(d["RunAtLoad"], True)
        self.assertIs(d["KeepAlive"], True)
        self.assertEqual(d["StandardOutPath"], LOG)
        self.assertEqual(d["WorkingDirectory"], "/home/strata")   # the root, as in the unit

    def test_plist_holds_no_tilde(self):
        # launchd expands neither ~ nor $HOME, in ProgramArguments or in the log path.
        d = plistlib.loads(autostart.render("darwin", "~/Strata/serve/server.py", "~/Strata/c.json",
                                            "~/Strata-data/logs/strata.log").encode("utf-8"))
        self.assertEqual(d["ProgramArguments"][1],
                         os.path.join(os.path.expanduser("~"), "Strata/serve/server.py"))
        self.assertNotIn("~", d["StandardOutPath"])

    def test_both_oses_run_the_same_argv(self):
        # One launcher line, two containers: a fix to one has to land in the other.
        plist = plistlib.loads(self.plist(port=18080).encode("utf-8"))
        self.assertEqual(plist["ProgramArguments"], exec_start(self.unit(port=18080)))

    def unit(self, **kw):
        return autostart.render("linux", SERVER, CONFIG, LOG, python_bin=VENV, **kw)


class TestEnableCommands(unittest.TestCase):
    def test_linux_enable_is_user_scope_and_lingers(self):
        # spec §11: "starts without anyone logging in" is loginctl enable-linger, not systemctl.
        # daemon-reload comes first: a unit file dropped into ~/.config/systemd/user is invisible
        # to the manager until it re-reads the directory, so enable alone can fail on a fresh write.
        enable, undo = autostart.enable_commands("linux")
        self.assertIn("--user", enable)
        self.assertIn("daemon-reload", enable)
        self.assertIn("systemctl --user enable", enable)
        self.assertIn("loginctl enable-linger", enable)
        self.assertIn("strata.service", enable)
        self.assertLess(enable.index("daemon-reload"), enable.index("systemctl --user enable"))

    def test_linux_undo_disables_both_halves(self):
        # Undoing only systemctl leaves linger on: the unit keeps booting for a user who asked
        # for it to stop, which is the opposite of "print how to undo it".
        enable, undo = autostart.enable_commands("linux")
        self.assertIn("systemctl --user disable", undo)
        self.assertIn("loginctl disable-linger", undo)

    def test_linux_linger_runs_even_when_the_start_job_fails(self):
        # The `&&` between systemctl and loginctl is the bug: measured on this box 2026-10-18,
        # `systemctl --user enable --now` exits 1 when the *start* job fails although the unit is
        # enabled. Gated, linger is skipped exactly then and spec §11's "starts without anyone
        # logging in" goes unmet with no error naming it. The stub fails the enable the way the
        # box does, so this runs the real line and reads which steps actually ran.
        enable, _undo = autostart.enable_commands("linux")
        calls, failed, rc = manager_calls_for(enable)
        self.assertIn("systemctl --user daemon-reload", calls)
        self.assertIn("systemctl --user enable --now strata.service", calls)
        # The enable really failed here — otherwise "linger ran" proves nothing about the gating.
        self.assertIn("systemctl --user enable --now strata.service", failed)
        self.assertIn("loginctl enable-linger " + STUB_USER, calls)
        # The documented price of `;`: the line's status is loginctl's, so a caller that wants the
        # systemctl failure as an exit code has to run the steps itself.
        self.assertEqual(rc, 0)

    def test_linux_undo_turns_linger_off_even_when_disable_fails(self):
        # The same coupling in reverse: a failed `disable` must not skip disable-linger, or linger
        # stays on for a unit that no longer exists. Enable and undo decouple symmetrically.
        _enable, undo = autostart.enable_commands("linux")
        calls, failed, _rc = manager_calls_for(undo)
        self.assertIn("systemctl --user disable --now strata.service", calls)
        self.assertIn("systemctl --user disable --now strata.service", failed)
        self.assertIn("loginctl disable-linger " + STUB_USER, calls)

    def test_mac_enable_and_undo_address_the_same_plist(self):
        enable, undo = autostart.enable_commands("darwin")
        self.assertIn("launchctl", enable)
        self.assertIn(autostart.unit_path("darwin"), enable)
        self.assertIn("launchctl", undo)
        self.assertIn(autostart.unit_path("darwin"), undo)

    def test_mac_undo_carries_w_or_the_agent_loads_again_at_login(self):
        # `load -w` overrides the agent's Disabled key; `man launchctl`: "-w Overrides the Disabled
        # key and sets it to false or true for the load and unload subcommands respectively". So
        # `unload` without -w removes the job for the current session only and the LaunchAgent is
        # back at the next login — the printed undo would have undone nothing.
        enable, undo = autostart.enable_commands("darwin")
        self.assertEqual(shlex.split(enable)[:3], ["launchctl", "load", "-w"])
        self.assertEqual(shlex.split(undo)[:3], ["launchctl", "unload", "-w"])

    def test_nothing_anywhere_needs_a_password(self):
        # spec §11: "neither needs a password" covers the commands, not just the unit text.
        for os_name in ("linux", "darwin"):
            for cmd in autostart.enable_commands(os_name):
                self.assertNotIn("sudo", cmd, "%s: %s" % (os_name, cmd))

    def test_the_pair_is_two_commands_a_human_can_run(self):
        for os_name in ("linux", "darwin"):
            pair = autostart.enable_commands(os_name)
            self.assertIsInstance(pair, tuple)
            self.assertEqual(len(pair), 2)
            for cmd in pair:
                self.assertIsInstance(cmd, str)
                self.assertTrue(cmd.strip())

    def test_an_unsupported_os_has_no_commands(self):
        with self.assertRaises(ValueError):
            autostart.enable_commands("windows")


class TestLlamaSwapProbe(unittest.TestCase):
    def test_probe_answers_true_only_while_a_listener_is_open(self):
        # Never 8090: llama-swap.service is live on this box, so a hardcoded-port assertion
        # passes on CI and fails here. Bind an ephemeral port and answer both directions.
        listener = socket.socket()
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            self.assertTrue(autostart.detect_llama_swap(port))
        finally:
            listener.close()
        self.assertFalse(autostart.detect_llama_swap(port))

    def test_probe_never_raises_on_a_port_it_cannot_use(self):
        # It reports "is anything listening"; a garbage port is a no, not a traceback, and must
        # not hang the installer. 70000 is out of range for sockaddr, None is not a port at all.
        for port in (1, 70000, None):
            self.assertIs(autostart.detect_llama_swap(port), False)

    def test_the_defaults_the_orchestrator_relies_on(self):
        # 8090 is where a llama-swap sits by default; 8095 is server.py's own default; python3
        # is the fallback interpreter when detect() had no venv to name.
        sig = inspect.signature(autostart.render)
        self.assertEqual(sig.parameters["port"].default, 8095)
        self.assertEqual(sig.parameters["python_bin"].default, "python3")
        self.assertEqual(inspect.signature(autostart.detect_llama_swap).parameters["port"].default,
                         8090)


if __name__ == "__main__":
    unittest.main()
