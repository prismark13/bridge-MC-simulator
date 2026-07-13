"""Single-suit combination analyzer — "the best way to play this suit".

Given the two declaring hands' holdings in one suit (e.g. AKxxx opposite Qxxx),
computes the a-priori probability of taking each number of tricks with best
(double-dummy) play: enumerate every way the missing cards split between the two
defenders, weight each by its a-priori likelihood, and solve the single suit
double-dummy for that layout. Pure — no redeal/DDS dependency; the suit solver
is a small self-contained minimax.

Model (the standard suit-combination convention): notrump, declarer has free
entries so may lead from either hand, defenders defend double-dummy. Position is
respected (N and S are the two hands as given; the finesse works or not by which
defender sits over the tenace). 'x' = a low spot; the defenders are given the
highest missing spot cards.
"""
from __future__ import annotations

import math
from functools import lru_cache
from itertools import combinations

RANKVAL = {"A": 14, "K": 13, "Q": 12, "J": 11, "T": 10,
           "9": 9, "8": 8, "7": 7, "6": 6, "5": 5, "4": 4, "3": 3, "2": 2}
VALRANK = {v: k for k, v in RANKVAL.items()}
_CLOCK = {"N": "E", "E": "S", "S": "W", "W": "N"}
_NS = frozenset({"N", "S"})


def _parse_hand(s: str):
    s = (s or "").upper().replace("10", "T")
    named, xs = [], 0
    for ch in s:
        if ch == "X":
            xs += 1
        elif ch in RANKVAL:
            named.append(RANKVAL[ch])
    return named, xs


def parse_combo(top: str, bottom: str):
    """('AKxxx', 'Qxxx') -> (top_ranks, bottom_ranks, missing_ranks) as ints.

    Named honours keep their rank; x's are filled with the lowest free spots, so
    the defenders hold the highest missing cards (standard 'x' convention)."""
    nt, xt = _parse_hand(top)
    nb, xb = _parse_hand(bottom)
    named = set(nt) | set(nb)
    remaining = sorted(set(range(2, 15)) - named)      # ascending
    our_x = remaining[:xt + xb]
    missing = remaining[xt + xb:]
    bx, tx = our_x[:xb], our_x[xb:xb + xt]
    top_ranks = sorted(nt + tx, reverse=True)
    bottom_ranks = sorted(nb + bx, reverse=True)
    return top_ranks, bottom_ranks, missing


def _solve(N, E, S, W) -> int:
    """Max NS tricks in the suit, double-dummy. Free entries: declarer leads the
    suit every round (from either hand); a defender winning a trick is just a lost
    trick, not the lead. Defenders defend double-dummy."""
    @lru_cache(maxsize=None)
    def start(n, e, s, w):
        hd = {"N": n, "E": e, "S": s, "W": w}
        best = -1                                      # declarer picks lead hand+card
        for ls in ("N", "S"):
            for card in hd[ls]:
                v = _trick(hd, ls, card)
                if v > best:
                    best = v
        return best if best >= 0 else 0                # declarer void -> no more tricks

    def _trick(hd, ls, lc):
        seq, p = [], _CLOCK[ls]
        for _ in range(3):
            seq.append(p); p = _CLOCK[p]
        return _follow(hd, {ls: lc}, seq, 0)

    def _follow(hd, played, seq, idx):
        if idx == 3:
            winner = max(played, key=lambda pl: played[pl])
            decl = 1 if winner in _NS else 0
            nn = tuple(c for c in hd["N"] if c != played.get("N"))
            ee = tuple(c for c in hd["E"] if c != played.get("E"))
            ss = tuple(c for c in hd["S"] if c != played.get("S"))
            ww = tuple(c for c in hd["W"] if c != played.get("W"))
            return decl + start(nn, ee, ss, ww)
        seat = seq[idx]
        if not hd[seat]:                               # void: can't follow
            return _follow(hd, played, seq, idx + 1)
        pick, best = (max, -1) if seat in _NS else (min, 10 ** 9)
        for card in hd[seat]:
            p2 = dict(played); p2[seat] = card
            best = pick(best, _follow(hd, p2, seq, idx + 1))
        return best

    return start(tuple(sorted(N)), tuple(sorted(E)),
                 tuple(sorted(S)), tuple(sorted(W)))


def suit_odds(top: str, bottom: str) -> dict:
    """Odds of each trick count for a suit combination.

    Returns {top, bottom, missing (str), dist {tricks: pct}, cum {tricks: pct at
    least}, max_tricks}. Enumerates all 2^missing defender splits, weighted by the
    a-priori probability of each split (vacant-space combinatorics)."""
    N, S, missing = parse_combo(top, bottom)
    m = len(missing)
    weight = {}
    for w in range(m + 1):
        wgt = math.comb(26 - m, 13 - w)                # any West set of size w
        for wset in combinations(missing, w):
            east = sorted(set(missing) - set(wset))
            t = _solve(N, east, S, list(wset))
            weight[t] = weight.get(t, 0) + wgt
    tot = sum(weight.values()) or 1
    dist = {k: 100 * v / tot for k, v in sorted(weight.items())}
    cum, run = {}, 0.0
    for k in sorted(dist, reverse=True):
        run += dist[k]
        cum[k] = run
    return {
        "top": "".join(VALRANK[r] for r in N),
        "bottom": "".join(VALRANK[r] for r in S),
        "missing": "".join(VALRANK[r] for r in sorted(missing, reverse=True)) or "—",
        "dist": dist, "cum": cum, "max_tricks": max(dist) if dist else 0,
        "ours": len(N) + len(S),
    }
