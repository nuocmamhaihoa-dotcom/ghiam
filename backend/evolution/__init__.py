"""Evolution Engine package — self-improving AI Sales OS lifecycle.

Import store/types directly to avoid circular imports with shadow_mode:
  from evolution.store import EvolutionStore
  from evolution.types import ...
  from evolution.engine import EvolutionEngine
"""

from __future__ import annotations

__all__ = [
    "EvolutionStore",
    "EvolutionEngine",
    "ProposalGenerator",
    "EvolutionQualityGate",
]


def __getattr__(name: str):
    if name == "EvolutionStore":
        from evolution.store import EvolutionStore

        return EvolutionStore
    if name == "EvolutionEngine":
        from evolution.engine import EvolutionEngine

        return EvolutionEngine
    if name == "ProposalGenerator":
        from evolution.proposals import ProposalGenerator

        return ProposalGenerator
    if name == "EvolutionQualityGate":
        from evolution.quality import EvolutionQualityGate

        return EvolutionQualityGate
    raise AttributeError(name)
