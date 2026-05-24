"""Substrate sub-agents — each owns a tick loop gated by an intensity dial.

Phase A ships stubs (Sentinel passes everything, Conductor holds state
with no policy) so the lifecycle and intensity machinery are *exercised*
from day one. Phase B+ swaps in real defense / decay / forecasting logic
without re-plumbing.

Exports land as the modules do — base class here, Sentinel/force-reject/
partition-maintenance/Conductor stubs land in later tasks.
"""

from substrate.agents.base import Level, SubAgent

__all__ = ["Level", "SubAgent"]
