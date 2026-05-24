"""Windows UTF-8 bootstrap for Hermes entry points.

Python on Windows has two long-standing text-encoding footguns:

1. ``sys.stdout`` / ``sys.stderr`` are bound to the console code page
   (``cp1252`` on US-locale installs), so ``print("café")`` crashes with
   ``UnicodeEncodeError: 'charmap' codec can't encode character``.

2. Child processes spawned via ``subprocess`` don't know to use UTF-8
   unless ``PYTHONUTF8`` and/or ``PYTHONIOENCODING`` are set in their
   environment — so any Python subprocess (the execute_code sandbox,
   delegation children, linter subprocesses, etc.) inherits the same
   cp1252 defaults and hits the same UnicodeEncodeError.

This module fixes both on Windows *only* — POSIX is untouched.  It
should be imported at the very top of every Hermes entry point
(``hermes``, ``hermes-agent``, ``hermes-acp``, ``python -m gateway.run``,
``batch_runner.py``, ``cron/scheduler.py``) before any other imports
that might do file I/O or print to stdout.

What this module does on Windows:

  - Sets ``os.environ["PYTHONUTF8"] = "1"`` (PEP 540 UTF-8 mode) so
    every child process we spawn uses UTF-8 for ``open()`` and stdio.
  - Sets ``os.environ["PYTHONIOENCODING"] = "utf-8"`` for belt-and-
    suspenders — some tools read this instead of / in addition to
    ``PYTHONUTF8``.
  - Reconfigures ``sys.stdout`` / ``sys.stderr`` to UTF-8 in the current
    process, using the ``reconfigure()`` API (Python 3.7+).  This fixes
    ``print("café")`` in the parent without a re-exec.

What this module does NOT do:

  - It does not re-exec Python with ``-X utf8``, so ``open()`` calls in
    the *current* process still default to locale encoding.  Those need
    an explicit ``encoding="utf-8"`` at the call site (lint rule
    ``PLW1514`` / ``PYI058``).  Ruff is the right tool for that sweep.

What this module does on POSIX:

  - Nothing.  POSIX systems are already UTF-8 by default in 99% of cases,
    and we don't want to touch ``LANG``/``LC_*`` behavior that users may
    have configured intentionally.  If someone hits a C/POSIX locale on
    Linux, they can export ``PYTHONUTF8=1`` themselves — we won't override.

Idempotent: safe to call multiple times.  ``_bootstrap_once`` guards
against double-reconfigure.
"""

from __future__ import annotations

import os
import sys

_IS_WINDOWS = sys.platform == "win32"
_bootstrap_applied = False


def apply_windows_utf8_bootstrap() -> bool:
    """Apply the Windows UTF-8 bootstrap if we're on Windows.

    Returns True if bootstrap was applied (i.e. we're on Windows and
    haven't already done this), False otherwise.  The return value is
    advisory — callers normally don't need it, but tests may want to
    assert the path was taken.

    Idempotent: subsequent calls after the first are a no-op.
    """
    global _bootstrap_applied

    if not _IS_WINDOWS:
        return False
    if _bootstrap_applied:
        return False

    # 1. Child processes inherit these and run in UTF-8 mode.
    #    We use setdefault() rather than overwriting so the user can
    #    explicitly opt out by setting PYTHONUTF8=0 in their environment
    #    (or PYTHONIOENCODING=something-else) if they really want to.
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    # 2. Reconfigure the current process's stdio to UTF-8.  Needed
    #    because os.environ changes don't retroactively rebind sys.stdout
    #    — those were bound at interpreter startup based on the console
    #    code page.  ``reconfigure`` is a TextIOWrapper method since 3.7.
    #
    #    errors="replace" means that if we ever *read* something from
    #    stdin that isn't UTF-8 (unlikely but possible with piped input
    #    from legacy tools), we'll get U+FFFD replacement chars rather
    #    than a crash.  Output is pure UTF-8.
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            # Not a TextIOWrapper (could be redirected to a BytesIO in
            # tests, or a non-standard stream in some embedded cases).
            # Skip silently — the env-var fix is still in effect for
            # child processes, which is the bigger win.
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Already closed, or someone replaced it with something
            # non-reconfigurable.  Non-fatal.
            pass

    # stdin is reconfigured separately with errors="replace" too — input
    # from a legacy pipe shouldn't crash the process.
    stdin = getattr(sys, "stdin", None)
    if stdin is not None:
        reconfigure = getattr(stdin, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass

    _bootstrap_applied = True
    return True


# Apply on import — entry points just need ``import hermes_bootstrap``
# (or ``from hermes_bootstrap import apply_windows_utf8_bootstrap``) at
# the very top of their module, before importing anything else.  The
# import side effect does the right thing.
apply_windows_utf8_bootstrap()


# ---------------------------------------------------------------------------
# PG pool bootstrap helper (Phase 0 Task 22)
# ---------------------------------------------------------------------------

_db_initialized = False


def init_db_sync() -> None:
    """Bootstrap helper: init PG pool from HERMES_PG_DSN; register atexit close.

    Idempotent — subsequent calls after the first are a no-op.  Sync entry
    points (run_agent.py, cli.py, cron/scheduler.py, acp_adapter/entry.py) call
    this once at the top of main().  Pure-async entry points (gateway/run.py)
    call ``await hermes_db.init(dsn)`` directly inside their asyncio.run().

    Raises RuntimeError if HERMES_PG_DSN is not set in the environment.
    """
    global _db_initialized
    if _db_initialized:
        return
    import asyncio
    import atexit
    import os
    import hermes_db

    dsn = os.environ.get("HERMES_PG_DSN")
    if not dsn:
        raise RuntimeError(
            "HERMES_PG_DSN must be set; export from .env or configure in environment"
        )
    asyncio.run(hermes_db.init(dsn))
    atexit.register(lambda: asyncio.run(hermes_db.close()) if hermes_db._pool else None)
    _db_initialized = True


# ---------------------------------------------------------------------------
# Substrate bootstrap helper (Phase A Task 14)
# ---------------------------------------------------------------------------
#
# Called after init_db_sync / hermes_db.init has populated the asyncpg pool.
# Pure-async entry points (gateway/run.py) ``await bootstrap_substrate()``
# from inside asyncio.run(); sync entry points use the sync wrapper which
# bridges via hermes_db.run_sync.
#
# Bootstrap failure is non-fatal: a logged warning, no substrate emission,
# Hermes runs as before (Phase A spec §0 — substrate failures must not
# crash Hermes).

_substrate_booted = False
_substrate_handle: "object | None" = None


async def bootstrap_substrate(log=None):
    """Boot the substrate and bind perception hooks. Idempotent.

    Returns the Substrate instance (or ``None`` if boot failed). Failures
    are logged as warnings and the function returns ``None`` so the
    caller (Hermes startup) can proceed without substrate emission.

    Tests that need a deterministic substrate construct it directly via
    ``Substrate.from_pool``; this helper is for production startup.
    """
    global _substrate_booted, _substrate_handle
    if _substrate_booted:
        return _substrate_handle
    import logging

    log = log or logging.getLogger("substrate.bootstrap")
    try:
        from substrate import Substrate

        substrate = await Substrate.boot(log=log)
        _substrate_handle = substrate
        _substrate_booted = True
        return substrate
    except Exception:
        log.exception("substrate.bootstrap.failed — Hermes continues without substrate emission")
        return None


def bootstrap_substrate_sync(log=None):
    """Sync facade for ``bootstrap_substrate``.

    Bridges via :func:`hermes_db.run_sync`. Must NOT be called from inside
    a running event loop — async entry points should ``await
    bootstrap_substrate(log)`` directly.
    """
    import hermes_db

    return hermes_db.run_sync(bootstrap_substrate(log=log))
