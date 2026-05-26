"""Regression for install.sh update-safety: re-running the installer against
an existing $HERMES_HOME must not clobber user-customized config.

Concretely, the installer can be re-run for two distinct purposes:

  1. **Fresh install** — first time on this machine. No $HERMES_HOME state
     exists; the installer creates ``.env``, ``config.yaml``, ``SOUL.md``,
     writes ``HERMES_PG_DSN`` based on the docker-compose port it picked,
     and runs the interactive setup wizard so the user can configure a
     provider/model/API key.

  2. **Update** — second-or-later run, typically to pull new code into the
     existing checkout (handled by ``clone_repo`` via ``git pull``). The
     user already configured everything; rewriting their ``HERMES_PG_DSN``
     (which may point at a remote PG cluster they bring) or re-running the
     wizard from scratch is hostile.

These tests pin the static invariants that protect update mode by
inspecting ``scripts/install.sh``. They don't run the installer end-to-end
(that needs Docker, root, a real PG, etc.) — they verify the script's
shape so regressions surface in seconds rather than during user upgrades.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


def _read_install_sh() -> str:
    return INSTALL_SH.read_text()


def _extract_function_body(name: str) -> str:
    text = _read_install_sh()
    match = re.search(
        rf"^{re.escape(name)}\(\)\s*\{{\s*\n(?P<body>.*?)^\}}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"{name}() not found in scripts/install.sh"
    return match["body"]


def test_detect_install_mode_function_exists() -> None:
    """The installer must call ``detect_install_mode`` early in ``main``
    so downstream functions can branch on ``IS_UPDATE``."""
    text = _read_install_sh()
    assert "detect_install_mode()" in text, (
        "detect_install_mode helper missing — without it the installer "
        "can't distinguish fresh installs from updates and will rewrite "
        "user config every time."
    )
    body = _extract_function_body("main")
    # detect_install_mode must run after resolve_install_layout (which
    # sets $INSTALL_DIR) and before any function that touches user state.
    assert "resolve_install_layout" in body
    assert "detect_install_mode" in body
    resolve_pos = body.index("resolve_install_layout")
    detect_pos = body.index("detect_install_mode")
    assert detect_pos > resolve_pos, (
        "detect_install_mode must run after resolve_install_layout so "
        "$INSTALL_DIR is set before we look for $INSTALL_DIR/.git."
    )


def test_run_setup_wizard_skips_when_provider_already_configured() -> None:
    """Update mode: don't re-prompt the wizard if .env already has an
    API key for one of the supported providers."""
    body = _extract_function_body("run_setup_wizard")
    assert "IS_UPDATE" in body, (
        "run_setup_wizard must consult $IS_UPDATE so it can skip on "
        "re-installs when the user already finished setup."
    )
    assert "_env_has_provider_api_key" in body, (
        "run_setup_wizard must call _env_has_provider_api_key (or "
        "equivalent) so updates with a configured provider skip the "
        "wizard. Otherwise every update re-prompts the user."
    )


def test_pg_dsn_rewrite_preserves_user_customized_dsns() -> None:
    """copy_config_templates must not rewrite HERMES_PG_DSN when the
    existing value points at a non-local cluster (the user almost
    certainly customized it to point at remote PG / Neon / Supabase /
    a host-network PG with custom creds)."""
    body = _extract_function_body("copy_config_templates")
    # Local-host detection: required so we can tell installer-managed
    # DSNs (safe to rewrite on port drift) from user-customized ones.
    assert "@localhost:" in body or "@127.0.0.1:" in body, (
        "copy_config_templates must detect localhost-style DSNs as a "
        "prerequisite to deciding whether HERMES_PG_DSN is installer-"
        "managed (safe to rewrite) or user-customized (preserve)."
    )
    # Escape hatch: --force-rewrite-config must be honoured so power
    # users can still flip back to installer-default DSN.
    assert "FORCE_REWRITE_CONFIG" in body, (
        "copy_config_templates must honour --force-rewrite-config "
        "(FORCE_REWRITE_CONFIG var) so users have an escape hatch when "
        "they intentionally want HERMES_PG_DSN reset to install default."
    )


def test_env_mutation_is_backed_up_first() -> None:
    """Any in-place sed against $HERMES_HOME/.env must be preceded by a
    backup. The grep is intentionally loose — we just want the backup
    helper to be called from copy_config_templates so a regression
    can't silently re-introduce blind rewrites."""
    body = _extract_function_body("copy_config_templates")
    # The single sed -i call against .env in this function rewrites
    # HERMES_PG_DSN. It must be preceded by a backup invocation.
    assert "_backup_env_file" in body, (
        "copy_config_templates uses ``sed -i`` against $HERMES_HOME/.env. "
        "It must call _backup_env_file first so users can recover from a "
        "bad rewrite. Without this, an installer bug silently destroys "
        "the user's .env."
    )


def test_force_rewrite_flag_documented_in_help() -> None:
    """The --force-rewrite-config escape hatch must be discoverable
    from ``install.sh --help`` — otherwise users hit the preserve-
    user-config path and have no obvious way to opt out."""
    text = _read_install_sh()
    assert "--force-rewrite-config" in text
    # Ensure it shows up in the help block (HELP_EOF), not just in the
    # arg parser switch.
    help_match = re.search(r"HELP_EOF(?P<help>.*?)HELP_EOF", text, re.DOTALL)
    assert help_match is not None, "install.sh --help block not found"
    assert "--force-rewrite-config" in help_match["help"], (
        "--force-rewrite-config must appear in the help block so users "
        "can discover the escape hatch via --help."
    )


# ---------------------------------------------------------------------------
# Substrate worker systemd unit — install.sh must install + enable it so
# new installs and upgrades both end up with a running worker without
# the operator having to know it exists. The worker is what runs the
# Sentinel/Curator tick loops; without it the substrate is inert. See
# scripts/install.sh::setup_substrate_worker_service and the writer/
# worker boot-split commit.
# ---------------------------------------------------------------------------


def test_setup_substrate_worker_service_function_exists() -> None:
    """install.sh ships a function that installs the substrate-worker
    systemd unit. Without it, fresh installs come up with sub-agents
    that aren't ticking."""
    text = _read_install_sh()
    assert "setup_substrate_worker_service()" in text, (
        "install.sh must define setup_substrate_worker_service; without "
        "it the substrate worker subprocess is not installed and "
        "Sentinel/Curator don't tick after install."
    )


def test_setup_substrate_worker_service_called_from_main() -> None:
    """The function must be wired into main() — defining it without
    calling it would be a silent regression."""
    body = _extract_function_body("main")
    assert "setup_substrate_worker_service" in body, (
        "setup_substrate_worker_service defined but not called from "
        "main(). The unit will never be installed."
    )


def test_substrate_worker_unit_uses_install_paths() -> None:
    """The rendered unit must reference the install's actual paths
    (INSTALL_DIR for ExecStart, HERMES_HOME for EnvironmentFile) so
    operators with custom --hermes-home / --cli-name don't get a broken
    unit pointing at someone else's home directory."""
    body = _extract_function_body("setup_substrate_worker_service")
    # ExecStart resolves to the actual venv python in this install.
    assert "$INSTALL_DIR/venv/bin/python" in body or \
           "$python_path" in body, (
        "Unit's ExecStart must use $INSTALL_DIR-derived python path; "
        "hardcoding ~/.hermes/hermes-agent breaks custom-dir installs."
    )
    # EnvironmentFile points at $HERMES_HOME/.env, not %h/.hermes/.env.
    assert "EnvironmentFile=$env_file" in body or \
           "EnvironmentFile=$HERMES_HOME" in body, (
        "Unit's EnvironmentFile must use $HERMES_HOME-derived path so "
        "operators with custom --hermes-home get a working unit."
    )


def test_substrate_worker_update_path_restarts_if_active() -> None:
    """On update, if the worker unit was already active, restart it
    (to pick up new code + unit changes). Don't enable a unit the
    operator chose to disable, and don't start one they chose to stop."""
    body = _extract_function_body("setup_substrate_worker_service")
    assert "was_active" in body, (
        "setup_substrate_worker_service must capture prior is-active "
        "state before mutating the unit; otherwise the update path "
        "can't distinguish 'restart this' from 'leave alone'."
    )
    assert "was_enabled" in body, (
        "setup_substrate_worker_service must capture prior is-enabled "
        "state so operator-disabled units stay disabled across updates."
    )
    assert "systemctl $scope restart" in body or \
           "systemctl $scope enable --now" in body, (
        "setup_substrate_worker_service must restart-if-active and "
        "enable-now-on-fresh; neither path can be missing."
    )


def test_substrate_worker_skips_cleanly_without_systemd() -> None:
    """Termux / non-Linux / no-systemctl environments must not error
    out — install.sh should print manual steps and continue."""
    body = _extract_function_body("setup_substrate_worker_service")
    assert 'DISTRO" = "termux"' in body, (
        "Termux skip-path missing; Termux has no systemd."
    )
    assert "command -v systemctl" in body, (
        "systemctl presence check missing; some Linux containers "
        "(distroless, scratch) ship without systemctl and the install "
        "must still succeed there."
    )
