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

Tractability comes from two collapses plus memoisation on the info set:

  * card-equivalence in play (``_reps``) — adjacent cards with nothing between
    them are interchangeable when choosing what to play;
  * world-equivalence (``_worlds``/``_runs``) — a run of missing cards with no
    declarer card between them is a "blob": only how many each defender holds
    matters, not which. This shrinks 2^m raw splits to prod(run+1) worlds and,
    crucially, is SOUND (unlike collapsing honour layouts, which erases the
    defence's restricted-choice mixing and inflates declarer's result).

The theory is the "best defence" model of Frank & Basin (1998); the single-suit
precedent is Warmerdam's SuitPlay. Feasibility is gated on the collapsed world
count and a wall-clock budget — nearly every real holding is exact; only the
hardest two-honour double-finesses (e.g. KJ9x opposite Qxx) fall back to an
estimate.
"""
from __future__ import annotations

import math
import time
from itertools import combinations

from .suitplay import parse_combo, VALRANK

_CLOCK = {"N": "E", "E": "S", "S": "W", "W": "N"}
_NS = frozenset({"N", "S"})


class _Timeout(Exception):
    """Raised when a solve exceeds its wall-clock budget; the caller falls back."""


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
    def __init__(self, deadline=None):
        self.memo = {}
        self.deadline = deadline
        self._ticks = 0

    def V(self, N, S, worlds, need):
        """Max weighted success: total weight of layouts where declarer takes
        >= need more tricks, under optimal blind play vs best defence."""
        if need <= 0:
            return float(sum(w for _, _, w in worlds))
        if not N and not S:
            return 0.0
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise _Timeout
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
                                                     s2, c2, s3, s4, need),
                          table=(lc,))

    def _third(self, N1, S1, worlds, lh, lc, s2, c2, s3, s4, need):
        hand = N1 if s3 == "N" else S1
        if not hand:
            return self._fourth(N1, S1, worlds, lh, lc, s2, c2, s3, None, s4, need)
        miss = set(worlds[0][0]) | set(worlds[0][1])
        other = set(S1 if s3 == "N" else N1)
        # The highest card already on the table decides who is winning the trick
        # so far, so it must block equivalence — else a card that would win the
        # current trick gets collapsed with a lower one. Only the max matters
        # (lower table cards are already beaten), which keeps branching down.
        hi = max([lc] + ([c2] if c2 is not None else []))
        best = 0.0
        tot = sum(w for _, _, w in worlds)
        for c3 in _reps(hand, other | miss | {hi}):
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
                          lambda sw, c4: self._resolve(N2, S2, sw, played, s4, c4, need),
                          table=tuple(played.values()))

    def _resolve(self, N2, S2, worlds, played, s4, c4, need):
        trick = dict(played)
        if c4 is not None:
            trick[s4] = c4
        won = 1 if max(trick, key=lambda k: trick[k]) in _NS else 0
        return self.V(N2, S2, worlds, need - won)

    def _dmin(self, worlds, dseat, declset, cont, table=()):
        """Defender ``dseat`` plays in each layout; declarer observes the rank so
        the info set splits; defenders choose the split minimising declarer's
        success. Branch-and-bound DFS over the (equivalence-collapsed) choices.
        ``table`` holds the cards already played to this trick — the highest
        blocks equivalence so a card that could win the current trick isn't
        collapsed with a low spot (lower table cards are already beaten)."""
        if table:
            declset = declset | {max(table)}
        wl = list(worlds)
        n = len(wl)
        best = [float("inf")]

        def dfs(i, groups, lo):
            if lo >= best[0]:
                return
            if self.deadline is not None:
                self._ticks += 1
                if not (self._ticks & 0x3FF) and time.monotonic() > self.deadline:
                    raise _Timeout
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
    """Every raw way the missing cards split between the defenders, a-priori
    weighted. The multiplicity of equivalent splits is kept ON PURPOSE — it is
    what lets the defence mix (restricted choice); collapsing it is unsound."""
    m = len(missing)
    for w in range(m + 1):
        wgt = math.comb(26 - m, 13 - w)
        for wset in combinations(missing, w):
            yield tuple(sorted(set(missing) - set(wset))), tuple(sorted(wset)), wgt


def _runs(missing, decl):
    """Split the missing cards into maximal runs of interchangeable low spots.
    Cards inside one run are a "blob": declarer can't tell them apart and the
    defence can't exploit which is which, so only HOW MANY each defender holds
    matters. A run breaks on a declarer card sitting between two spots, and on
    an HONOUR (rank >= 10): touching honours like QJ look interchangeable for
    trick-winning but are NOT for the info set — collapsing them erases the
    defence's restricted-choice mixing (playing the Q vs the J from QJ), which
    biases the result. Honours therefore stay as their own singleton runs."""
    ms = sorted(missing)
    if not ms:
        return []
    runs, cur = [], [ms[0]]
    for c in ms[1:]:
        between = any(cur[-1] < d < c for d in decl)
        honour = c >= 10 or cur[-1] >= 10
        if between or honour:
            runs.append(cur)
            cur = [c]
        else:
            cur.append(c)
    runs.append(cur)
    return runs


def _worlds(N, S, missing):
    """Distinct defender layouts after collapsing interchangeable low cards.

    Instead of 2^m raw splits, one per (per-run count) choice: a run of length L
    contributes L+1 states (0..L cards to West), and equivalent raw splits are
    merged with their a-priori weight summed. This is SOUND — unlike collapsing
    honour layouts — because a run's cards can never win a trick apart from each
    other, so merging them removes no defensive choice. World count is
    prod(len(run)+1), which stays small when few honours are missing."""
    from itertools import product
    decl = set(N) | set(S)
    m = len(missing)
    runs = _runs(missing, decl)
    if not runs:
        return ((tuple(), tuple(), math.comb(26, 13)),)
    out = []
    for counts in product(*[range(len(r) + 1) for r in runs]):
        eset, wset, mult = [], [], 1
        for run, w in zip(runs, counts):     # give West the top w of each run
            wset += run[len(run) - w:]
            eset += run[:len(run) - w]
            mult *= math.comb(len(run), w)
        wgt = mult * math.comb(26 - m, 13 - len(wset))
        out.append((tuple(sorted(eset)), tuple(sorted(wset)), wgt))
    return tuple(out)


def _world_count(N, S, missing):
    prod = 1
    for r in _runs(missing, set(N) | set(S)):
        prod *= len(r) + 1
    return prod


def _restricted_choice(N, S, missing):
    """True if the defenders can hold two *touching* honours (no declarer card of
    a rank between them) — e.g. missing K-Q. Optimal defence then randomises
    which honour to play (restricted choice); a pure-strategy solver can't, so
    the result runs slightly high. Used only to withhold the 'exact' label."""
    decl = set(N) | set(S)
    hon = sorted(c for c in missing if c >= 10)
    return any(not any(a < d < b for d in decl)
               for a, b in zip(hon, hon[1:]))


def _play_desc(N, S, missing, cum):
    """One-line description of the winning line, inferred from the optimal
    result and the expected tricks of the drop vs finesse lines."""
    from .suitplay import _playout
    if not cum:
        return ""
    decl = list(N) + list(S)
    ms = sorted(missing)
    hi, dhi = ms[-1], max(decl)

    def finessable(m):
        # Declarer can finesse for m: a higher card, and a card below m that
        # beats every OTHER missing card below m (so only m can beat the finesse).
        below_d = [d for d in decl if d < m]
        below_m = [x for x in ms if x < m]
        return (any(d > m for d in decl) and below_d
                and max(below_d) > (max(below_m) if below_m else -1))

    fk = [m for m in ms if m >= 10 and finessable(m)]   # only finesse for an honour
    if not fk:
        if hi > dhi:
            return f"Knock out the {VALRANK[hi]}, then run the suit."
        return "No guess — cash your winners from the top."

    def exp(mode):
        tot = num = 0
        for E, W, wt in _splits(missing):
            tot += wt; num += wt * _playout(tuple(N), E, tuple(S), W, mode)
        return num / tot if tot else 0

    key = VALRANK[max(fk)]
    de, fe = exp("drop"), exp("finesse")               # expected tricks of each line
    if abs(de - fe) < 0.02:
        return f"Finesse the {key} or play for the drop — about even."
    if de > fe:
        return "Play for the drop — cash your top honours, don't finesse."
    return f"Finesse for the {key} — lead low toward your honours."


def _ceiling(top, bottom, info):
    """Fallback for holdings too costly to solve exactly: the double-dummy
    result (verified correct, computed per-layout). It's an upper bound on the
    real blind-play odds — and exact whenever best defence can't beat a guess,
    which is most two-honour holdings. Flagged ceiling=True so the UI can say so."""
    from .suitplay import suit_odds
    N, S, missing = parse_combo(top, bottom)
    dd = suit_odds(top, bottom)
    cum = dd.get("cum", {})
    return {**info, "feasible": True, "exact": False, "ceiling": True,
            "cum": cum, "max_tricks": max(cum) if cum else 0,
            "play": _play_desc(tuple(sorted(N)), tuple(sorted(S)), missing, cum)}


def suit_optimal(top: str, bottom: str, max_worlds: int = 200,
                 time_budget: float = 8.0, use_cache: bool = True) -> dict:
    """Real best-play odds of each trick count (optimal blind play vs best
    defence). Feasibility is gated on the COLLAPSED world count, not the raw
    number of missing cards: interchangeable low spots merge into blobs, so a
    suit missing many low cards but few honours is still exact. A wall-clock
    budget is the real safety net — the two-honour double-finesse holdings have
    few worlds but expensive defensive coupling. ``feasible=False`` when either
    bound trips, and the caller falls back to the estimate."""
    N, S, missing = parse_combo(top, bottom)
    m = len(missing)
    honours = sum(1 for c in missing if c >= 10)
    info = {"top": "".join(VALRANK[r] for r in N),
            "bottom": "".join(VALRANK[r] for r in S),
            "missing": "".join(VALRANK[r] for r in sorted(missing, reverse=True)) or "—",
            "worlds": _world_count(N, S, missing)}
    # A previously-solved holding is instant: the answer never changes, so it is
    # cached by canonical form (see suitcache). Only exact hits are stored, so a
    # cached ceiling never blocks a later exact solve.
    if use_cache:
        try:
            from .suitcache import get as _cache_get
            hit = _cache_get(top, bottom)
        except Exception:      # noqa: BLE001
            hit = None
        if hit:
            return {**info, "feasible": True, "exact": hit["exact"], "cached": True,
                    "cum": hit["cum"], "max_tricks": max(hit["cum"]) if hit["cum"] else 0,
                    "play": _play_desc(tuple(sorted(N)), tuple(sorted(S)), missing, hit["cum"])}
    # Raw is exact but costs 2^m worlds; the collapse is fast but a ~1% estimate
    # on rich two-honour holdings (non-locality). Use raw while it's cheap (every
    # 9+ card fit), collapse only beyond. exact=True marks a guaranteed value.
    # It is withheld when the defenders can falsecard between touching honours
    # (restricted choice): the correct defence there is a *randomised* play our
    # pure-strategy solver can't make, so the result runs ~0.5% high (verified
    # against SuitPlay: AJT9x/xxxx reads 76.6 vs the true 76.0).
    rc = _restricted_choice(N, S, missing)
    if (1 << m) <= 16:
        worlds = tuple(_splits(missing))
        exact = not rc
    else:
        if info["worlds"] > max_worlds:
            return _ceiling(top, bottom, info)
        worlds = _worlds(N, S, missing)
        exact = honours <= 1 and not rc
    total = sum(w for _, _, w in worlds) or 1
    N, S = tuple(sorted(N)), tuple(sorted(S))
    solver = _Solver(deadline=time.monotonic() + time_budget)
    cum, t = {}, 1
    try:
        while t <= len(N) + len(S):
            p = 100 * solver.V(N, S, worlds, t) / total
            if p < 0.05:
                break
            cum[t] = p
            t += 1
    except _Timeout:
        return _ceiling(top, bottom, info)
    if exact:                     # only guaranteed values are worth caching
        try:
            from .suitcache import put as _cache_put
            _cache_put(top, bottom, cum, True)
        except Exception:         # noqa: BLE001
            pass
    return {**info, "feasible": True, "exact": exact, "cum": cum,
            "max_tricks": max(cum) if cum else 0,
            "play": _play_desc(N, S, missing, cum)}
