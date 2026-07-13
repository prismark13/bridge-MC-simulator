"""Optimal single-suit play — the SuitPlay problem, solved exactly.

Computes the best line's real chance of each trick count: declarer plays
optimally WITHOUT seeing the defenders' cards (its play is constant across every
layout it can't tell apart), against best (double-dummy) defence — including the
defenders' right to *unblock* to wreck a finesse.

This is a one-sided imperfect-information game (only declarer is blind). The
solver is an information-set min-max:

  * declarer node  — choose a card, the SAME across every layout in the info set;
  * defender node  — each layout's defender plays a card; declarer observes it,
    so the info set splits by rank; the defenders pick the split that minimises
    declarer's success (this coupling is the crux of the problem).

Tractability comes from card-equivalence collapsing (adjacent cards with nothing
between them are interchangeable) plus memoisation on the info set. Exact for the
holdings people actually analyse (opponents up to ~6 cards); callers fall back to
an estimate beyond that.
"""
from __future__ import annotations

import math
from itertools import combinations

from .suitplay import parse_combo, VALRANK

_CLOCK = {"N": "E", "E": "S", "S": "W", "W": "N"}
_NS = frozenset({"N", "S"})


def _rm(t, c):
    i = t.index(c)
    return t[:i] + t[i + 1:]


def _reps(hand, blockers):
    """Distinct cards worth playing from ``hand``: the lowest of each run of
    consecutive cards with no blocker (other in-play card) between them."""
    h = sorted(hand)
    out = []
    for i, c in enumerate(h):
        if i == 0 or any(h[i - 1] < b < c for b in blockers):
            out.append(c)
    return out


class _Solver:
    def __init__(self):
        self.memo = {}

    def V(self, N, S, worlds, need):
        """Max weighted success: total weight of layouts where declarer takes
        >= need more tricks, under optimal blind play vs best defence."""
        if need <= 0:
            return float(sum(w for _, _, w in worlds))
        if not N and not S:
            return 0.0
        key = (N, S, worlds, need)
        got = self.memo.get(key)
        if got is not None:
            return got
        miss = set(worlds[0][0]) | set(worlds[0][1])     # defender cards in play
        best = 0.0
        for lh, hand in (("N", N), ("S", S)):
            other = set(S if lh == "N" else N)
            for lc in _reps(hand, other | miss):
                v = self._lead(N, S, worlds, lh, lc, need)
                if v > best:
                    best = v
                    if best >= sum(w for _, _, w in worlds):
                        break
            if best >= sum(w for _, _, w in worlds):
                break
        self.memo[key] = best
        return best

    def _lead(self, N, S, worlds, lh, lc, need):
        N1, S1 = (_rm(N, lc), S) if lh == "N" else (N, _rm(S, lc))
        _, s2, s3, s4 = (lambda o: [o, _CLOCK[o], _CLOCK[_CLOCK[o]],
                                    _CLOCK[_CLOCK[_CLOCK[o]]]])(lh)
        return self._dmin(worlds, s2, set(N1) | set(S1),
                          lambda sw, c2: self._third(N1, S1, sw, lh, lc,
                                                     s2, c2, s3, s4, need))

    def _third(self, N1, S1, worlds, lh, lc, s2, c2, s3, s4, need):
        hand = N1 if s3 == "N" else S1
        if not hand:
            return self._fourth(N1, S1, worlds, lh, lc, s2, c2, s3, None, s4, need)
        miss = set(worlds[0][0]) | set(worlds[0][1])
        other = set(S1 if s3 == "N" else N1)
        best = 0.0
        tot = sum(w for _, _, w in worlds)
        for c3 in _reps(hand, other | miss):
            v = self._fourth(N1, S1, worlds, lh, lc, s2, c2, s3, c3, s4, need)
            if v > best:
                best = v
                if best >= tot:
                    break
        return best

    def _fourth(self, N1, S1, worlds, lh, lc, s2, c2, s3, c3, s4, need):
        N2, S2 = N1, S1
        if c3 is not None:
            N2, S2 = (_rm(N1, c3), S1) if s3 == "N" else (N1, _rm(S1, c3))
        played = {lh: lc}
        if c2 is not None:
            played[s2] = c2
        if c3 is not None:
            played[s3] = c3
        return self._dmin(worlds, s4, set(N2) | set(S2),
                          lambda sw, c4: self._resolve(N2, S2, sw, played, s4, c4, need))

    def _resolve(self, N2, S2, worlds, played, s4, c4, need):
        trick = dict(played)
        if c4 is not None:
            trick[s4] = c4
        won = 1 if max(trick, key=lambda k: trick[k]) in _NS else 0
        return self.V(N2, S2, worlds, need - won)

    def _dmin(self, worlds, dseat, declset, cont):
        """Defender ``dseat`` plays in each layout; declarer observes the rank so
        the info set splits; defenders choose the split minimising declarer's
        success. Branch-and-bound DFS over the (equivalence-collapsed) choices."""
        wl = list(worlds)
        n = len(wl)
        best = [float("inf")]

        def dfs(i, groups, lo):
            if lo >= best[0]:
                return
            if i == n:
                s = 0.0
                for cv, mem in groups.items():
                    s += cont(tuple(sorted(mem)), cv)
                    if s >= best[0]:
                        return
                if s < best[0]:
                    best[0] = s
                return
            E, W, wt = wl[i]
            dc = E if dseat == "E" else W
            if not dc:
                g = dict(groups); g[None] = g.get(None, ()) + ((E, W, wt),)
                dfs(i + 1, g, lo)
                return
            other = W if dseat == "E" else E
            for c in _reps(dc, declset | set(other)):
                nd = _rm(dc, c)
                nw = (nd, W, wt) if dseat == "E" else (E, nd, wt)
                g = dict(groups); g[c] = g.get(c, ()) + (nw,)
                dfs(i + 1, g, lo)
        dfs(0, {}, 0.0)
        return best[0]


def _splits(missing):
    m = len(missing)
    for w in range(m + 1):
        wgt = math.comb(26 - m, 13 - w)
        for wset in combinations(missing, w):
            yield tuple(sorted(set(missing) - set(wset))), tuple(sorted(wset)), wgt


def suit_optimal(top: str, bottom: str, max_missing: int = 7) -> dict:
    """Real best-play odds of each trick count (optimal blind play vs best
    defence). Returns None-ish (``feasible=False``) if the combination is too big
    to solve exactly within ``max_missing`` defender cards."""
    N, S, missing = parse_combo(top, bottom)
    info = {"top": "".join(VALRANK[r] for r in N),
            "bottom": "".join(VALRANK[r] for r in S),
            "missing": "".join(VALRANK[r] for r in sorted(missing, reverse=True)) or "—"}
    if len(missing) > max_missing:
        return {**info, "feasible": False}
    worlds = tuple(_splits(missing))
    total = sum(w for _, _, w in worlds) or 1
    N, S = tuple(sorted(N)), tuple(sorted(S))
    solver = _Solver()
    cum, t = {}, 1
    while t <= len(N) + len(S):
        p = 100 * solver.V(N, S, worlds, t) / total
        if p < 0.05:
            break
        cum[t] = p
        t += 1
    return {**info, "feasible": True, "cum": cum,
            "max_tricks": max(cum) if cum else 0}
