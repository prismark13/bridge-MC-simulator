"""Exact single-suit solver by vector propagation (Frank, Basin & Bundy, AAAI 2000).

This replaces the information-set minimax in ``suitplay_opt``, which was wrong in
structure: it searched over *partitions of the information set* (which world
plays which card), exponential in the number of worlds, and it suffered strategy
fusion. vec-prop does neither.

The idea (AAAI 2000, "Combining Knowledge and Search to Solve Single-Suit
Bridge"): a MAX strategy is a choice of exactly one branch at each MAX node. Its
value is an n-tuple — declarer's trick count in each of the n possible worlds.
Rather than evaluate strategies one at a time, back up *sets* of such vectors, so
one bottom-up pass yields every strategy's per-world payoff:

    leaf      ->  { payoff-vector }
    MAX node  ->  union of the daughters' vector sets
                  ("no single branch is selected; the results of all possible MAX
                   branch selections are retained")
    MIN node  ->  Cartesian product: one vector from each daughter, each
                  combination reduced per-world by Equation (1)

Equation (1): in each world, the defenders take the *minimum* over the branches
they can actually play in that world ("MIN branches can only be followed by
East/West if they hold the appropriate cards").

Because a strategy commits to one branch at each MAX node, declarer never plays
differently in worlds it cannot distinguish — strategy fusion is impossible by
construction. Defenders choose per world, which is exactly the best-defence
model (they see all cards).

The vector sets are exponential in principle (finding optimal strategies against
best defence is NP-complete in the tree size), so we apply the paper's pruning:
drop any vector that is pointwise <= another at the same node. The authors report
this suffices to solve all 1561 Encyclopedia suit combinations, averaging ~0.6s.

Worlds are the collapsed ones from ``suitplay_opt._worlds`` — the paper does the
same ("If we don't distinguish between the nine low cards in this problem, there
are 20 distinct outcomes of the initial deal").
"""
from __future__ import annotations

import time
from itertools import product

from .suitplay import parse_combo, VALRANK

_CLOCK = {"N": "E", "E": "S", "S": "W", "W": "N"}
_NS = frozenset({"N", "S"})
_INF = None                    # marks a world that cannot reach this branch


class Timeout(Exception):
    pass


def _rm(t, c):
    i = t.index(c)
    return t[:i] + t[i + 1:]


def _reps(hand, blockers):
    """Distinct cards worth playing: the lowest of each run of consecutive cards
    with no blocker between them (equivalent cards are interchangeable)."""
    h = sorted(hand)
    out = []
    for i, c in enumerate(h):
        if i == 0 or any(h[i - 1] < b < c for b in blockers):
            out.append(c)
    return out


def _pareto(vecs):
    """Drop vectors pointwise <= another (the paper's pruning step). ``None``
    entries mark unreachable worlds and compare as neutral."""
    out = []
    for v in vecs:
        dominated = False
        keep = []
        for u in out:
            if _dominates(u, v):
                dominated = True
                break
            if not _dominates(v, u):
                keep.append(u)
        if not dominated:
            keep.append(v)
            out = keep
    return out


def _dominates(u, v):
    """True if u >= v pointwise, so v is an inevitably inferior strategy and can
    be discarded. Worlds unreachable in v (None) impose no constraint."""
    for a, b in zip(u, v):
        if b is None:
            continue
        if a is None or a < b:
            return False
    return True


class _Solver:
    def __init__(self, n, runid, deadline=None):
        self.n = n
        # rank -> id of its equivalence run among the missing cards. Defenders'
        # branches are keyed by RUN, not by rank: cards of one run have no
        # declarer card between them, so they are the same observation to
        # declarer and win/lose the trick identically. Keying by raw rank would
        # let declarer tell apart worlds whose low spots differ — handing it
        # information it does not have, which reads back as double-dummy.
        self.runid = runid
        self.deadline = deadline
        self.memo = {}

    def _tick(self):
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise Timeout

    def solve(self, N, S, wstate, active):
        """Vector set for the position: declarer to lead a fresh trick.
        ``wstate`` is a tuple over all worlds of (E, W) remaining; ``active`` is
        the frozenset of world indices still possible on this line of play."""
        self._tick()
        if (not N and not S) or not active:
            return [tuple(0 if i in active else None for i in range(self.n))]
        key = (N, S, wstate, active)
        got = self.memo.get(key)
        if got is not None:
            return got
        miss = set()
        for i in active:
            e, w = wstate[i]
            miss |= set(e) | set(w)
        out = []
        for lh, hand in (("N", N), ("S", S)):          # MAX: union over branches
            other = set(S if lh == "N" else N)
            for lc in _reps(hand, other | miss):
                out.extend(self._lead(N, S, wstate, active, lh, lc))
        out = _pareto(out)
        self.memo[key] = out
        return out

    def _lead(self, N, S, wstate, active, lh, lc):
        N1, S1 = (_rm(N, lc), S) if lh == "N" else (N, _rm(S, lc))
        _, s2, s3, s4 = (lambda o: [o, _CLOCK[o], _CLOCK[_CLOCK[o]],
                                    _CLOCK[_CLOCK[_CLOCK[o]]]])(lh)
        return self._dmin(N1, S1, wstate, active, s2,
                          lambda ws, ac, c2: self._third(N1, S1, ws, ac, lh, lc,
                                                         s2, c2, s3, s4))

    def _third(self, N1, S1, wstate, active, lh, lc, s2, c2, s3, s4):
        hand = N1 if s3 == "N" else S1
        if not hand:
            return self._fourth(N1, S1, wstate, active, lh, lc, s2, c2, s3, None, s4)
        miss = set()
        for i in active:
            e, w = wstate[i]
            miss |= set(e) | set(w)
        other = set(S1 if s3 == "N" else N1)
        hi = max([lc] + ([c2] if c2 is not None else []))
        out = []
        for c3 in _reps(hand, other | miss | {hi}):     # MAX: union
            out.extend(self._fourth(N1, S1, wstate, active, lh, lc,
                                    s2, c2, s3, c3, s4))
        return _pareto(out)

    def _fourth(self, N1, S1, wstate, active, lh, lc, s2, c2, s3, c3, s4):
        N2, S2 = N1, S1
        if c3 is not None:
            N2, S2 = (_rm(N1, c3), S1) if s3 == "N" else (N1, _rm(S1, c3))
        played = {lh: lc}
        if c2 is not None:
            played[s2] = c2
        if c3 is not None:
            played[s3] = c3
        return self._dmin(N2, S2, wstate, active, s4,
                          lambda ws, ac, c4: self._resolve(N2, S2, ws, ac,
                                                           played, s4, c4))

    def _resolve(self, N2, S2, wstate, active, played, s4, c4):
        trick = dict(played)
        if c4 is not None:
            trick[s4] = c4
        won = 1 if max(trick, key=lambda k: trick[k]) in _NS else 0
        sub = self.solve(N2, S2, wstate, active)
        if not won:
            return sub
        return [tuple(None if x is None else x + 1 for x in v) for v in sub]

    def _dmin(self, N_, S_, wstate, active, dseat, cont):
        """MIN node. Branches are the defender's distinct plays; a world may only
        follow a branch whose card it holds. Take one vector from each branch
        (Cartesian product) and combine per-world by the minimum over the
        branches that world can actually play."""
        self._tick()
        # Group active worlds by the RUN the defender plays from. Every card of a
        # run is the same observation and resolves the trick identically, so one
        # branch per run is exactly declarer's information.
        branches = {}          # run id (or None = void) -> {world: card played}
        for i in active:
            e, w = wstate[i]
            dc = e if dseat == "E" else w
            if not dc:
                branches.setdefault(None, {})[i] = None
                continue
            for rid in {self.runid[c] for c in dc}:
                # play the lowest card held in that run (they are equivalent)
                card = min(c for c in dc if self.runid[c] == rid)
                branches.setdefault(rid, {})[i] = card
        sets, keys, reps = [], [], []
        for rid, plays in branches.items():
            ws = list(wstate)
            for i, card in plays.items():
                if card is None:
                    continue
                e, w = ws[i]
                ws[i] = (_rm(e, card), w) if dseat == "E" else (e, _rm(w, card))
            widx = frozenset(plays)
            rep = None if rid is None else max(plays.values())
            sets.append(cont(tuple(ws), widx, rep))
            keys.append(widx)
        if not sets:
            return [tuple(None for _ in range(self.n))]
        out = []
        for combo in product(*sets):        # one vector per branch
            v = []
            for i in range(self.n):
                vals = [vec[i] for vec, widx in zip(combo, keys)
                        if i in widx and vec[i] is not None]
                v.append(min(vals) if vals else None)
            out.append(tuple(v))
        return _pareto(out)


def _setup(top, bottom, time_budget):
    from .suitplay_opt import _worlds, _runs
    N, S, missing = parse_combo(top, bottom)
    N, S = tuple(sorted(N)), tuple(sorted(S))
    worlds = _worlds(N, S, missing)
    n = len(worlds)
    wstate = tuple((e, w) for e, w, _ in worlds)
    weights = [wt for _, _, wt in worlds]
    runid = {}
    for rid, run in enumerate(_runs(missing, set(N) | set(S))):
        for c in run:
            runid[c] = rid
    sv = _Solver(n, runid, deadline=time.monotonic() + time_budget)
    return N, S, missing, wstate, weights, sv, n


def _score(vecs, weights, n, k):
    """Best strategy's weighted total of worlds taking >= k tricks."""
    return max((sum(weights[i] for i in range(n)
                    if v[i] is not None and v[i] >= k) for v in vecs), default=0)


def openings(top: str, bottom: str, goal: int, time_budget: float = 30.0):
    """Each distinct opening play ranked by its exact chance of ``goal`` tricks.
    The best one IS the recommended line — no heuristic guessing."""
    N, S, missing, wstate, weights, sv, n = _setup(top, bottom, time_budget)
    total = sum(weights) or 1
    active = frozenset(range(n))
    miss = set(missing)
    out = []
    for lh, hand in (("N", N), ("S", S)):
        other = set(S if lh == "N" else N)
        for lc in _reps(hand, other | miss):
            vecs = sv._lead(N, S, wstate, active, lh, lc)
            out.append((100.0 * _score(vecs, weights, n, goal) / total, lh, lc))
    out.sort(key=lambda x: -x[0])
    return out


def describe(N, S, lh, lc, missing):
    """Describe the opening the solver actually chose, by its ROLE — so that
    equivalent low cards (the 4 and the 6 from the same hand) read the same and
    don't split a tie into two spurious descriptions."""
    other = S if lh == "N" else N
    ms = sorted(missing)
    if not any(m > lc for m in ms):                  # nothing out beats it
        return f"Cash the {VALRANK[lc]}."
    tenace = sorted((c for c in other if c >= 10), reverse=True)
    if tenace and any(m < max(tenace) for m in ms):   # leading toward honours
        held = "".join(VALRANK[c] for c in tenace)
        return f"Lead low toward the {held} — finesse."
    return f"Lead the {VALRANK[lc]}."


def suit_vec(top: str, bottom: str, time_budget: float = 30.0) -> dict:
    """Exact trick-count distribution by vector propagation, plus the real line."""
    N, S, missing, wstate, weights, sv, n = _setup(top, bottom, time_budget)
    total = sum(weights) or 1
    vecs = sv.solve(N, S, wstate, frozenset(range(n)))
    maxt = len(N) + len(S)
    cum = {}
    for k in range(1, maxt + 1):
        p = 100.0 * _score(vecs, weights, n, k) / total
        if p < 0.05:
            break
        cum[k] = p
    # The line comes from the solver, not a heuristic. Several openings often tie
    # (the finesse-vs-drop decision comes later, not on the first card), so only
    # claim a single start when the optimal openings all mean the same thing.
    play, lines = "", []
    if cum:
        top_goal = max(cum)
        raw = openings(top, bottom, top_goal, time_budget)
        # collapse equivalent openings (same description) to their best value
        seen = {}
        for pct, lh, lc in raw:
            d = describe(N, S, lh, lc, missing)
            if d not in seen or pct > seen[d]:
                seen[d] = pct
        lines = sorted(((p, d) for d, p in seen.items()), key=lambda x: -x[0])
        if lines:
            best = lines[0][0]
            tied = [d for p, d in lines if p >= best - 0.005]
            play = tied[0] if len(tied) == 1 else \
                "Equally good starts: " + " / ".join(d.rstrip(".") for d in tied) + "."
    # hands are held sorted ascending internally; display them high-to-low
    return {"top": "".join(VALRANK[r] for r in sorted(N, reverse=True)),
            "bottom": "".join(VALRANK[r] for r in sorted(S, reverse=True)),
            "missing": "".join(VALRANK[r] for r in sorted(missing, reverse=True)) or "—",
            "worlds": n, "strategies": len(vecs), "cum": cum,
            "max_tricks": max(cum) if cum else 0, "exact": True, "play": play,
            "lines": lines}
