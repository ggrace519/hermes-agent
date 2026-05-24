"""Substrate sub-agents — each owns a tick loop gated by an intensity dial.

Phase A ships stubs (Sentinel passes everything, Conductor holds state
with no policy) so the lifecycle and intensity machinery are *exercised*
from day one. Phase B+ swaps in real defense / decay / forecasting logic
without re-plumbing.

Exports land as the modules do — base class in Task 8, Sentinel in Task
9, force-reject in Task 10, partition-maintenance in Task 11, Conductor
in Task 12.
"""

__all__: list[str] = []
