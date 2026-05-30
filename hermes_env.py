"""hermes→thoth environment-variable compatibility bridge.

Phase 2 of the hermes→thoth rename. The project is renaming, but existing
installs set ``HERMES_*`` env vars (in ``~/.hermes/.env`` and the shell) and
~398 reads across the codebase still use ``os.environ.get("HERMES_...")``.
Rewriting every call site is high-risk churn; instead we mirror the two
spellings in ``os.environ`` once at startup so:

* a user can set EITHER ``THOTH_X`` (new, canonical) or ``HERMES_X`` (legacy),
* every existing ``HERMES_*`` reader keeps working unchanged, and
* new code may read ``THOTH_*`` directly.

Design (agreed by two independent model reviews):

* **Two-way mirror, ``THOTH_`` canonical.** Only-``HERMES_`` set → copy to
  ``THOTH_``. Only-``THOTH_`` set → copy to ``HERMES_``. Both set & differ →
  ``THOTH_`` wins (legacy overwritten). Never delete either spelling.
* **Empty-string guard.** An empty ``THOTH_X`` does NOT clobber a non-empty
  ``HERMES_X`` (avoids a stray ``THOTH_X=`` wiping a deployment value).
* **Idempotent** — safe to call repeatedly (gateway hot-reload re-runs it).
* **Pure stdlib, import-safe.** Imported from ``hermes_bootstrap`` before
  anything heavy; must not import ``dotenv``/``yaml``/``hermes_constants``.

Scope: the home directory (``HERMES_HOME`` / ``THOTH_HOME``) is deliberately
EXCLUDED here and owned by Phase 3 (the ``~/.thoth`` home-dir migration),
which consolidates ``hermes_constants.get_hermes_home()``, adds ``THOTH_HOME``
with auto-migration, and handles the subprocess-propagation + ``.env``-path
chicken-and-egg cases that are specific to the home dir. Mirroring ``HOME``
here would create a stale ``THOTH_HOME`` that THOTH-wins normalization could
use to clobber a freshly-set ``HERMES_HOME`` in a forked profile subprocess —
so we leave it for Phase 3 to handle correctly.
"""

from __future__ import annotations

import os
from typing import MutableMapping, Optional

_LEGACY_PREFIX = "HERMES_"
_CANONICAL_PREFIX = "THOTH_"

# Var-name suffixes owned by Phase 3 (home dir) — NOT mirrored here.
# e.g. HERMES_HOME, HERMES_HOME_MODE.
_HOME_DEFERRED = ("HOME",)


def _is_deferred(base: str) -> bool:
    return any(base == d or base.startswith(d + "_") for d in _HOME_DEFERRED)


def normalize_thoth_env(env: Optional[MutableMapping[str, str]] = None) -> int:
    """Mirror ``HERMES_X`` <-> ``THOTH_X`` in *env* (default ``os.environ``).

    ``THOTH_`` is canonical and wins when both are set with different values,
    except an empty ``THOTH_X`` never overwrites a non-empty ``HERMES_X``.
    Idempotent. Returns the number of keys written (0 when already consistent).

    Only names beginning exactly with ``HERMES_`` / ``THOTH_`` are touched, so
    private sentinels like ``_HERMES_GATEWAY`` (leading underscore) are skipped
    automatically. Home-dir vars are deferred to Phase 3 (see module docstring).
    """
    if env is None:
        env = os.environ

    # Collect the distinct bases present under either prefix (snapshot the keys
    # so we never mutate the mapping while iterating it).
    bases = set()
    for key in list(env.keys()):
        if key.startswith(_CANONICAL_PREFIX):
            bases.add(key[len(_CANONICAL_PREFIX):])
        elif key.startswith(_LEGACY_PREFIX):
            bases.add(key[len(_LEGACY_PREFIX):])

    changed = 0
    for base in bases:
        if not base or _is_deferred(base):
            continue
        legacy_key = _LEGACY_PREFIX + base
        canon_key = _CANONICAL_PREFIX + base
        legacy_val = env.get(legacy_key)
        canon_val = env.get(canon_key)

        # Choose the authoritative value: THOTH_ wins, but an empty THOTH_
        # must not clobber a non-empty HERMES_.
        if canon_val is not None and not (
            canon_val == "" and legacy_val not in (None, "")
        ):
            authoritative = canon_val
        elif legacy_val is not None:
            authoritative = legacy_val
        else:
            continue

        if legacy_val != authoritative:
            env[legacy_key] = authoritative
            changed += 1
        if canon_val != authoritative:
            env[canon_key] = authoritative
            changed += 1

    return changed


def sync_thoth_aliases(
    keys, env: Optional[MutableMapping[str, str]] = None
) -> int:
    """Force each given key's twin spelling to match the key's CURRENT value.

    Use after a ``.env`` load (or secret-source injection) where *keys* are the
    names that source just set: the freshly-resolved value is authoritative and
    must overwrite any stale mirrored alias from an earlier normalization.

    Without this, the general THOTH-wins :func:`normalize_thoth_env` reverts a
    rotated legacy value: load #1 mirrors ``HERMES_X=old``→``THOTH_X=old``; a
    later ``.env`` reload sets ``HERMES_X=new``; THOTH-wins would then restore
    the stale ``old``. Here the file is the source of truth for the keys it set.
    Returns the number of twin keys written. Idempotent. Home vars deferred.
    """
    if env is None:
        env = os.environ
    changed = 0
    for key in list(keys):
        if key.startswith(_CANONICAL_PREFIX):
            base = key[len(_CANONICAL_PREFIX):]
            twin = _LEGACY_PREFIX + base
        elif key.startswith(_LEGACY_PREFIX):
            base = key[len(_LEGACY_PREFIX):]
            twin = _CANONICAL_PREFIX + base
        else:
            continue
        if not base or _is_deferred(base):
            continue
        value = env.get(key)
        if value is None:
            continue
        if env.get(twin) != value:
            env[twin] = value
            changed += 1
    return changed
