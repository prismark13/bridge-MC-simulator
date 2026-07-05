"""Double-dummy solver + par calculation.

Wraps DDS's batched ``CalcAllTables`` (32 tables/call, all cores) and its
``Par`` (optimal competitive contract + score, doubled sacrifices included)
behind a small :class:`Solver` protocol. :class:`DdsSolver` owns its own
parameter buffer, so two solver instances don't step on each other.
"""
from __future__ import annotations

from ctypes import POINTER, Structure, byref, c_byte, c_char, c_int
from typing import Protocol, Sequence

from redeal import dds as _rdds

from ..domain.contracts import STRAINS

BATCH = 32
_SI = {"S": 0, "H": 1, "D": 2, "C": 3, "N": 4}

# DDS "vulnerable" enum for Par: 0 None, 1 Both, 2 N/S, 3 E/W.
VUL_TO_DDS = {"None": 0, "Both": 1, "NS": 2, "EW": 3}

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


class _ParResults(Structure):
    _fields_ = [("parScore", (c_char * 16) * 2),
                ("parContractsString", (c_char * 128) * 2)]


_rdds.dll.CalcAllTables.argtypes = [
    POINTER(_TDeals), c_int, c_int * 5, POINTER(_TablesRes), POINTER(_ParBuf)]
_rdds.dll.Par.argtypes = [POINTER(_TRes), POINTER(_ParResults), c_int]
try:
    _rdds.dll.SetMaxThreads(0)
except Exception:
    pass
_TRUMP_FILTER = (c_int * 5)(0, 0, 0, 0, 0)


def _cstr(arr):
    return bytes(arr).split(b"\0", 1)[0].decode(errors="replace")


class Solver(Protocol):
    def solve(self, deals: Sequence) -> list: ...


class DdsSolver:
    """DD-solve batches of deals (and optionally compute par). Owns its buffer."""

    def __init__(self):
        self._par_buf = _ParBuf()

    def _tables(self, deals: Sequence) -> _TablesRes:
        if len(deals) > BATCH:
            raise ValueError(f"at most {BATCH} deals per call, got {len(deals)}")
        dd = _TDeals(); dd.noOfTables = len(deals)
        for i, deal in enumerate(deals):
            for seat, hand in enumerate(deal):
                for suit, holding in enumerate(hand):
                    dd.deals[i].cards[seat][suit] = \
                        sum(1 << r.value for r in holding)
        res = _TablesRes()
        if _rdds.dll.CalcAllTables(byref(dd), -1, _TRUMP_FILTER, byref(res),
                                   byref(self._par_buf)) != 1:
            raise RuntimeError("CalcAllTables failed")
        return res

    @staticmethod
    def _tricks(tres) -> dict:
        rt = tres.resTable
        return {s: (rt[_SI[s]][0], rt[_SI[s]][1], rt[_SI[s]][2], rt[_SI[s]][3])
                for s in STRAINS}

    def _par(self, tres, vul: int):
        """-> {ns, ew, contract, sac} or None. Scores are signed, side-relative."""
        pr = _ParResults()
        if _rdds.dll.Par(byref(tres), byref(pr), vul) != 1:
            return None
        s0 = _cstr(pr.parScore[0]).split()      # e.g. ['NS', '1520']
        s1 = _cstr(pr.parScore[1]).split()      # e.g. ['EW', '-1520']

        def num(parts):
            if len(parts) > 1 and parts[1].lstrip("-").isdigit():
                return int(parts[1])
            return 0
        contract = _cstr(pr.parContractsString[0])
        contract = contract.split(":", 1)[-1].strip()   # 'NS:NS 7N' -> 'NS 7N'
        return {"ns": num(s0), "ew": num(s1), "contract": contract,
                "sac": "x" in contract.lower()}

    def solve(self, deals: Sequence) -> list:
        """Per deal -> {strain: (tN, tE, tS, tW)}. Up to BATCH deals per call."""
        res = self._tables(deals)
        return [self._tricks(res.results[i]) for i in range(len(deals))]

    def solve_full(self, deals: Sequence, par_vul: int | None = None) -> list:
        """Per deal -> (tricks_dict, par_dict|None). ``par_vul`` is a DDS vul int."""
        res = self._tables(deals)
        out = []
        for i in range(len(deals)):
            tres = res.results[i]
            par = self._par(tres, par_vul) if par_vul is not None else None
            out.append((self._tricks(tres), par))
        return out


_DEFAULT: DdsSolver | None = None


def default_solver() -> DdsSolver:
    """Process-wide default solver (lazily created)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = DdsSolver()
    return _DEFAULT
