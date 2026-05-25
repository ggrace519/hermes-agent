"""Tests for hermes_cli.cli_name — resolving the user-facing launcher name.

A Substrate fork installed side-by-side under a different launcher name
(e.g. ``hermes-substrate``) must echo that name back in resume/setup hints,
not the hardcoded ``hermes`` (which may point at the upstream install).
"""

import sys

import pytest

from hermes_cli.cli_name import cli_name


class TestCliName:
    def test_env_var_is_authoritative(self, monkeypatch):
        """The launcher shim's HERMES_CLI_NAME wins: the shim execs the venv
        console script (named "hermes"), so argv[0] can't be trusted to carry
        the name the user actually typed."""
        monkeypatch.setattr(sys, "argv", ["/opt/venv/bin/hermes", "--resume", "x"])
        monkeypatch.setenv("HERMES_CLI_NAME", "hermes-substrate")
        assert cli_name() == "hermes-substrate"

    def test_argv0_basename_when_no_env(self, monkeypatch):
        """Without the env var (dev checkout, renamed standalone exe), fall
        back to argv[0]."""
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/hermes-substrate", "--resume", "x"])
        monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
        assert cli_name() == "hermes-substrate"

    def test_plain_hermes(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["/home/u/.local/bin/hermes"])
        monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
        assert cli_name() == "hermes"

    def test_strips_windows_exe_suffix(self, monkeypatch):
        # Bare basename keeps the test platform-independent (os.path.basename
        # only splits on backslash under ntpath, not posixpath).
        monkeypatch.setattr(sys, "argv", ["hermes-substrate.exe"])
        monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
        assert cli_name() == "hermes-substrate"

    @pytest.mark.parametrize("argv0", [
        "/usr/lib/python3.11/site-packages/hermes_cli/__main__.py",
        "__main__.py",
        "main.py",
        "/usr/bin/python3",
        "python",
        "pytest",
        "-c",
        "",
    ])
    def test_module_and_interpreter_launches_fall_through_to_env(self, monkeypatch, argv0):
        """Module/interpreter launches don't reflect a command name, so the
        launcher-provided env var is consulted."""
        monkeypatch.setattr(sys, "argv", [argv0])
        monkeypatch.setenv("HERMES_CLI_NAME", "hermes-substrate")
        assert cli_name() == "hermes-substrate"

    def test_default_when_nothing_resolvable(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["__main__.py"])
        monkeypatch.delenv("HERMES_CLI_NAME", raising=False)
        # sys.executable is a python interpreter → also skipped → default.
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3.11")
        assert cli_name() == "hermes"

    def test_env_basename_only(self, monkeypatch):
        """A path in HERMES_CLI_NAME is reduced to its basename."""
        monkeypatch.setattr(sys, "argv", ["python"])
        monkeypatch.setenv("HERMES_CLI_NAME", "/opt/bin/hermes-substrate")
        assert cli_name() == "hermes-substrate"

    def test_blank_env_falls_through_to_argv0(self, monkeypatch):
        """An empty/whitespace env var is ignored in favor of argv[0]."""
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/hermes-fork"])
        monkeypatch.setenv("HERMES_CLI_NAME", "   ")
        assert cli_name() == "hermes-fork"
