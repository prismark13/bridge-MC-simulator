"""Simulation engine: DDS solving, deal sampling, the Monte-Carlo loop.

Depends on the domain layer and on redeal (for deal generation + the bundled
DDS DLL). Exposes :func:`run` — the UI-agnostic entry point — plus the
:class:`DdsSolver` that owns its own native buffers.
"""
from .simulate import run
from .solver import BATCH, DdsSolver, Solver, default_solver

__all__ = ["run", "BATCH", "DdsSolver", "Solver", "default_solver"]
