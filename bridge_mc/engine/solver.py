"""Double-dummy solver.

Wraps DDS's batched ``CalcAllTables`` (32 tables/call, all cores) behind a
small :class:`Solver` protocol. :class:`DdsSolver` owns its own parameter
buffer, so two solver instances can run without stepping on each other — the
previous module-level ``_PAR`` global made concurrent solving unsafe.
"""
from __future__ import annotations

from ctypes import POINTER, Structure, byref, c_byte, c_int
from typing import Protocol, Sequence

from redeal import dds as _rdds

from ..domain.contracts import STRAINS

BATCH = 32
_SI = {"S": 0, "H": 1, "D": 2, "C": 3, "N": 4}

# A per-strain -> (tN, tE, tS, tW) mapping for one deal.
TrickTable = dict


class _TDeal(Structure):
    _fields_ = [("cards", c_int * 4 * 4)]


class _TDeals(Structure):
    _fields_ = [("noOfTables", c_int), ("deals", _TDeal * BATCH)]


class _TRes(Structure):
    _fields_ = [("resTable", c_int * 4 * 5)]


class _TablesRes(Structure):
    _fields_ = [("noOfBoards", c_int), ("results", _TRes * BATCH)]


class _ParBuf(Structure):
    _fields_ = [("_b", c_byte * (BATCH * 1024))]


_rdds.dll.CalcAllTables.argtypes = [
    POINTER(_TDeals), c_int, c_int * 5, POINTER(_TablesRes), POINTER(_ParBuf)]
try:
    _rdds.dll.SetMaxThreads(0)
except Exception:
    pass
_TRUMP_FILTER = (c_int * 5)(0, 0, 0, 0, 0)


class Solver(Protocol):
    def solve(self, deals: Sequence) -> list: ...


class DdsSolver:
    """DD-solve batches of deals. Owns its own native parameter buffer."""

    def __init__(self):
        self._par = _ParBuf()

    def solve(self, deals: Sequence) -> list:
        """Per deal -> {strain: (tN, tE, tS, tW)}. Up to BATCH deals per call."""
        if len(deals) > BATCH:
            raise ValueError(f"solve() takes at most {BATCH} deals, got {len(deals)}")
        dd = _TDeals(); dd.noOfTables = len(deals)
        for i, deal in enumerate(deals):
            for seat, hand in enumerate(deal):
                for suit, holding in enumerate(hand):
                    dd.deals[i].cards[seat][suit] = \
                        sum(1 << r.value for r in holding)
        res = _TablesRes()
        if _rdds.dll.CalcAllTables(byref(dd), -1, _TRUMP_FILTER, byref(res),
                                   byref(self._par)) != 1:
            raise RuntimeError("CalcAllTables failed")
        out = []
        for i in range(len(deals)):
            rt = res.results[i].resTable
            out.append({s: (rt[_SI[s]][0], rt[_SI[s]][1],
                            rt[_SI[s]][2], rt[_SI[s]][3]) for s in STRAINS})
        return out


_DEFAULT: DdsSolver | None = None


def default_solver() -> DdsSolver:
    """Process-wide default solver (lazily created)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = DdsSolver()
    return _DEFAULT
