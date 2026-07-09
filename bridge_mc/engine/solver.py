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


# --- endplay backend --------------------------------------------------------
# redeal bundles a slow DDS build (≈6× slower on Linux); endplay ships an
# optimized prebuilt wheel. Same DDS engine underneath, so trick tables and par
# are bit-identical — this is purely a faster binary. Used when importable
# (e.g. the Linux container); otherwise we fall back to redeal's DdsSolver.
_RMAP = "..23456789TJQKA"     # redeal card .value (2..14) -> PBN rank char


def _pbn_suit(holding) -> str:
    return "".join(_RMAP[c.value] for c in sorted(holding, key=lambda c: -c.value))


def _pbn(hands) -> str:
    """[N, E, S, W] redeal Hands -> a PBN deal string."""
    return "N:" + " ".join(
        ".".join(_pbn_suit(s) for s in (h.spades, h.hearts, h.diamonds, h.clubs))
        for h in hands)


class EndplaySolver:
    """Drop-in for :class:`DdsSolver` backed by endplay's optimized DDS.

    Same interface (``solve`` / ``solve_full``) and identical results; converts
    redeal hands to endplay deals via PBN, reuses the DD table for par."""

    def __init__(self):
        from endplay.dds import calc_all_tables, par
        from endplay.types import Deal, Denom, Penalty, Player, Vul
        self._calc, self._parfn, self._Deal = calc_all_tables, par, Deal
        self._players = (Player.north, Player.east, Player.south, Player.west)
        self._north = Player.north
        self._denom = {"C": Denom.clubs, "D": Denom.diamonds, "H": Denom.hearts,
                       "S": Denom.spades, "N": Denom.nt}
        self._dch = {Denom.clubs: "C", Denom.diamonds: "D", Denom.hearts: "H",
                     Denom.spades: "S", Denom.nt: "N"}
        self._pen = {Penalty.passed: "", Penalty.doubled: "x", Penalty.redoubled: "xx"}
        self._pl = {Player.north: "N", Player.east: "E",
                    Player.south: "S", Player.west: "W"}
        # DDS vul int (0 None, 1 Both, 2 NS, 3 EW) -> endplay Vul.
        self._vul = {0: Vul.none, 1: Vul.both, 2: Vul.ns, 3: Vul.ew}

    def _tricks(self, tab) -> dict:
        return {s: tuple(int(tab[self._denom[s], p]) for p in self._players)
                for s in STRAINS}

    def _side(self, decs) -> str:
        s = {self._pl[p] for p in decs}
        if s == {"N", "S"}:
            return "NS"
        if s == {"E", "W"}:
            return "EW"
        return "".join(sorted(s))

    def _par(self, tab, vul_int: int):
        pr = self._parfn(tab, self._vul[vul_int], self._north)
        groups: dict = {}
        sac = False
        for c in pr:
            groups.setdefault((c.level, c.denom, c.penalty), set()).add(c.declarer)
            if self._pen[c.penalty]:
                sac = True
        alts = [f"{self._side(decs)} {lvl}{self._dch[den]}{self._pen[pen]}"
                for (lvl, den, pen), decs in groups.items()]
        return {"ns": pr.score, "ew": -pr.score,
                "contract": ",".join(alts), "sac": sac}

    def solve(self, deals: Sequence) -> list:
        tabs = self._calc([self._Deal.from_pbn(_pbn(h)) for h in deals])
        return [self._tricks(t) for t in tabs]

    def solve_full(self, deals: Sequence, par_vul: int | None = None) -> list:
        tabs = self._calc([self._Deal.from_pbn(_pbn(h)) for h in deals])
        return [(self._tricks(t), self._par(t, par_vul) if par_vul is not None else None)
                for t in tabs]


_DEFAULT = None


def default_solver():
    """Process-wide default solver (lazily created). Prefers the faster endplay
    backend when it's importable, else redeal's bundled DDS."""
    global _DEFAULT
    if _DEFAULT is None:
        try:
            _DEFAULT = EndplaySolver()
        except Exception:
            _DEFAULT = DdsSolver()
    return _DEFAULT
