"""Cognitive substrate — the medium the agent inhabits.

See [Phase A spec](https://github.com/ggrace519/llm-cognitive-thought/blob/main/docs/superpowers/specs/2026-05-22-phase-a-substrate-skeleton.md)
for what this package is and what it deliberately is NOT (Phase A is
write-only L0 perception emission; no recall, no curator, no LLM-driven
sub-agents).

Public surface (will grow as Phase A tasks land):

    from substrate import Substrate  # available after Task 14

    async def main():
        await hermes_db.init(dsn)       # Phase 0 pool
        sub = await Substrate.boot(log) # substrate skeleton on top
        ...
        await sub.shutdown()
        await hermes_db.close()

Hooks live in ``substrate.events.hermes_hooks`` and are bound to the
booted Substrate instance via ``Substrate.boot()``.
"""

# ``Substrate`` is exported once ``substrate.facade`` lands (Phase A
# plan Task 14). Until then importing ``substrate`` only gives access
# to the storage types and enums, which is enough for the migration
# code (Task 4) and the type tests (Task 3) to land cleanly.

__all__: list[str] = []
