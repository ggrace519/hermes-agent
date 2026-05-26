"""``hermes embed`` — embedding admin commands.

Surface:

    hermes embed reshape <DIM>     # reshape pgvector column + re-embed

Distinct from ``hermes substrate`` (read-only inspection): these commands
mutate the substrate's embedding state. Lives at the top level rather
than under ``hermes substrate`` because embedding is its own user-visible
concern (config, model choice, dim, cost) — not just substrate internals.

Future expansion (not in this PR):
    hermes embed status            # coverage, cost since last reset, last error
    hermes embed backfill          # force a re-embed pass over the NULL queue
    hermes embed test              # 1-call probe of the configured provider

Wired into Hermes's top-level argparse via :func:`register_subparser`
called from ``hermes_cli/main.py``.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    import asyncpg


# ---------------------------------------------------------------------------
# Subparser registration — called from hermes_cli/main.py.
# ---------------------------------------------------------------------------


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Add the ``hermes embed`` subcommand tree to ``subparsers``."""
    embed_parser = subparsers.add_parser(
        "embed",
        help="Embedding admin (reshape pgvector column, re-embed)",
        description="Admin commands for the substrate's embedding column. "
        "Inspection lives under ``hermes substrate recall``; this namespace "
        "is for state-mutating operations.",
    )
    embed_sub = embed_parser.add_subparsers(dest="embed_command")

    reshape_p = embed_sub.add_parser(
        "reshape",
        help="Reshape pgvector column to a new dimension + re-embed all slices",
        description=(
            "Change the substrate_slices.embedding column from its current "
            "vector(N) shape to vector(<DIM>). Existing embeddings are NOT "
            "convertible across dims and are cleared; the slices are then "
            "re-embedded inline using the configured provider (see "
            "auxiliary.embedding.* in config.yaml). Interactive y/N prompt "
            "before any destructive work — pass --yes to skip."
        ),
    )
    reshape_p.add_argument(
        "dim",
        type=int,
        help="Target embedding dimension (1-16000, pgvector cap). Must match "
        "the configured model's native output dim — see the dim guard in "
        "substrate/recall/embeddings.py.",
    )
    reshape_p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip the y/N confirmation prompt.",
    )
    reshape_p.add_argument(
        "--no-reembed",
        action="store_true",
        help="Reshape the column only; don't re-embed inline. The Curator's "
        "background backfill will re-populate on its normal cadence (slower; "
        "useful for non-interactive setups).",
    )
    reshape_p.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Slices per embedding-API call during the re-embed pass "
        "(default 50; lower if hitting provider rate limits).",
    )
    reshape_p.set_defaults(func=_cmd_embed_reshape)

    embed_parser.set_defaults(func=_cmd_embed_help)


def _cmd_embed_help(args: argparse.Namespace) -> int:
    """Default for ``hermes embed`` with no subcommand."""
    print(
        "usage: hermes embed reshape <DIM> [--yes] [--no-reembed] [--batch-size N]",
        file=sys.stderr,
    )
    return 2


# ---------------------------------------------------------------------------
# reshape command — sync wrapper that bridges to the async implementation.
# ---------------------------------------------------------------------------


def _cmd_embed_reshape(args: argparse.Namespace) -> int:
    """Validate args, prompt for confirmation, then drive the reshape."""
    import asyncio

    import hermes_db

    target = args.dim
    if target < 1 or target > 16000:
        print(
            f"error: dim must be between 1 and 16000 (got {target})",
            file=sys.stderr,
        )
        return 2

    if not hermes_db.ensure_pool_sync():
        print(
            "error: HERMES_PG_DSN not set; cannot connect to substrate PG.",
            file=sys.stderr,
        )
        return 1

    try:
        return asyncio.get_event_loop().run_until_complete(
            _reshape_async(
                target=target,
                interactive=not args.yes,
                reembed=not args.no_reembed,
                batch_size=args.batch_size,
            )
        )
    except RuntimeError:
        # No running loop — make one.
        return asyncio.run(
            _reshape_async(
                target=target,
                interactive=not args.yes,
                reembed=not args.no_reembed,
                batch_size=args.batch_size,
            )
        )


async def _reshape_async(
    *,
    target: int,
    interactive: bool,
    reembed: bool,
    batch_size: int,
) -> int:
    """Reshape the embedding column, optionally re-embed inline."""
    import hermes_db

    # 1. Read current schema dim.
    current = await _current_schema_dim()
    if current is None:
        print(
            "error: substrate_slices.embedding column not found. Run "
            "``alembic upgrade head`` first.",
            file=sys.stderr,
        )
        return 1

    # Count embedded + unembedded so the confirmation shows real numbers.
    async with hermes_db.connection() as conn:
        embedded = await conn.fetchval(
            "SELECT count(*) FROM substrate_slices WHERE embedding IS NOT NULL"
        ) or 0
        unembedded = await conn.fetchval(
            "SELECT count(*) FROM substrate_slices WHERE embedding IS NULL"
        ) or 0

    if current == target:
        print(f"Embedding column is already vector({target}); nothing to do.")
        if unembedded > 0 and reembed:
            print(
                f"Note: {unembedded} slice(s) still have NULL embeddings. "
                "Run ``hermes embed reshape {target} --no-reembed=false`` "
                "to force the backfill, or wait for the Curator's "
                "background loop."
            )
        return 0

    # 2. Confirm.
    total = embedded + unembedded
    print(
        f"About to reshape substrate_slices.embedding: "
        f"vector({current}) -> vector({target})"
    )
    print(f"  Existing embeddings: {embedded:,} (will be CLEARED)")
    print(f"  Total slices:        {total:,}")
    if reembed:
        print(
            f"  Re-embed inline:    yes (batch size {batch_size}, using the "
            "configured embedding provider — see substrate/recall/embeddings.py)"
        )
    else:
        print(
            "  Re-embed inline:    no (--no-reembed); Curator will backfill "
            "on its normal cadence"
        )
    print(
        "  Cost:               re-embed cost depends on your configured "
        "provider (free for local Ollama; metered for cloud providers)"
    )

    if interactive:
        try:
            ans = input("Continue? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in {"y", "yes"}:
            print("Aborted.")
            return 1

    # 3. Reshape (drop index, NULL embeddings, ALTER, recreate index).
    print(f"Reshaping column to vector({target}) ...")
    async with hermes_db.transaction() as conn:
        await conn.execute(
            "DROP INDEX IF EXISTS substrate_slices_embedding_cosine_idx"
        )
        await conn.execute("UPDATE substrate_slices SET embedding = NULL")
        await conn.execute(
            f"ALTER TABLE substrate_slices "
            f"ALTER COLUMN embedding TYPE vector({target})"
        )
        await conn.execute(
            "CREATE INDEX substrate_slices_embedding_cosine_idx "
            "ON substrate_slices USING ivfflat (embedding vector_cosine_ops) "
            "WITH (lists = 100)"
        )
    print(f"  Done: vector({current}) -> vector({target}), index rebuilt.")

    # Drop the embeddings module's cached dim so the next embed() call
    # picks up the new shape.
    try:
        from substrate.recall import embeddings as _embed_mod
        _embed_mod.reset_schema_dim_cache()
    except Exception:
        pass

    if not reembed:
        print(
            f"Reshape complete. {total:,} slice(s) marked for backfill — the "
            "Curator will re-embed them on its next tick (typically every "
            "30s during normal Hermes operation)."
        )
        return 0

    # 4. Inline re-embed pass with progress.
    return await _backfill_inline(total=total, batch_size=batch_size)


async def _current_schema_dim() -> int | None:
    """Read the live vector(N) dim from pg_catalog. Returns None if the
    column is missing or isn't a vector type."""
    import hermes_db

    async with hermes_db.connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT format_type(atttypid, atttypmod) AS coltype
              FROM pg_attribute
             WHERE attrelid = 'substrate_slices'::regclass
               AND attname  = 'embedding'
               AND NOT attisdropped
            """
        )
    if row is None:
        return None
    coltype = (row["coltype"] or "")
    if not coltype.startswith("vector("):
        return None
    try:
        return int(coltype[len("vector("):-1])
    except (ValueError, IndexError):
        return None


async def _backfill_inline(*, total: int, batch_size: int) -> int:
    """Re-embed every NULL-embedding slice. Print progress per batch.

    Returns 0 on full success, 1 if the provider failed at any point
    (the partial state is fine; Curator backfill picks up the rest).
    """
    import hermes_db

    # Late import — embed() needs the schema-dim cache cleared above.
    from substrate.recall.embeddings import embed
    # Reuse the same text extractor the Curator uses so re-embedded
    # vectors compare cleanly to fresh Curator-emitted vectors.
    from substrate.agents.curator import _extract_text_for_embedding

    if total == 0:
        print("No slices to embed.")
        return 0

    print(f"Re-embedding {total:,} slice(s) in batches of {batch_size} ...")
    done = 0
    failed = 0

    while True:
        # Pull a batch of NULL-embedding slice rows. Ordered for
        # deterministic resume-after-interrupt behaviour.
        async with hermes_db.connection() as conn:
            rows = await conn.fetch(
                """
                SELECT slice_id, ingest_time_world, payload
                  FROM substrate_slices
                 WHERE embedding IS NULL
                 ORDER BY ingest_time_world
                 LIMIT $1
                """,
                batch_size,
            )
        if not rows:
            break

        texts = [_extract_text_for_embedding(r["payload"]) for r in rows]
        try:
            vectors = await embed(texts)
        except Exception as exc:
            print(
                f"  embed() raised: {exc}. Aborting; "
                f"{done:,}/{total:,} re-embedded before failure.",
                file=sys.stderr,
            )
            return 1

        # Write back per row. Skip rows where embed() returned None
        # (provider failure for that item only).
        async with hermes_db.transaction() as conn:
            for r, vec in zip(rows, vectors):
                if vec is None:
                    failed += 1
                    continue
                await conn.execute(
                    """
                    UPDATE substrate_slices
                       SET embedding = $1
                     WHERE slice_id = $2
                       AND ingest_time_world = $3
                    """,
                    vec, r["slice_id"], r["ingest_time_world"],
                )
        done += len(rows)
        pct = (done / total * 100.0) if total else 100.0
        print(
            f"  Re-embedded {done:,}/{total:,} ({pct:.1f}%)"
            + (f", {failed} per-item failures" if failed else ""),
            flush=True,
        )

        # Tight provider call — small natural pause keeps us under any
        # provider's per-second rate cap without explicit throttling.
        # No sleep needed here; HTTP round-trip provides backoff.

    if failed:
        print(
            f"Done. {done - failed:,}/{total:,} re-embedded successfully; "
            f"{failed} slice(s) failed and remain NULL — the Curator's "
            "backfill loop will retry them.",
        )
        return 0 if failed < total else 1
    print(f"Done. {done:,}/{total:,} re-embedded successfully.")
    return 0
