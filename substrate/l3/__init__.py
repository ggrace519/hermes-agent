"""L3 — patterns and abstractions over L1/L2.

The Pattern-finder sub-agent (Phase E2) generalizes across many L1
extractions into higher-order observations: generalizations, recurring
themes, recurring structures. Distinct from L1 (explicit facts) and L2
(pairwise associations).
"""

from __future__ import annotations

from substrate.l3.schema import ParsedPattern, Pattern

__all__ = ["Pattern", "ParsedPattern"]
