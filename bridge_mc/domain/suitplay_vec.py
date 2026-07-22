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


def _pareto(vecs, tick=None):
    """Drop vectors pointwise <= another (the paper's pruning step). ``None``
    entries mark unreachable worlds and compare as neutral. ``tick`` is checked
    periodically so an intractable holding (a huge Pareto frontier) still hits its
    deadline instead of grinding for minutes.

    The dominance of each pair is decided in ONE pass (does u cover v, v cover u,
    or neither) rather than two function calls — this loop is the solver's hot
    spot on many-world holdings, so the inlining matters."""
    out = []
    for j, v in enumerate(vecs):
        if tick is not None and j & 1023 == 0:
            tick()
        dominated = False
        keep = []
        for u in out:
            u_ge = v_ge = True                 # u covers v?  v covers u?
            for a, b in zip(u, v):
                if b is not None and (a is None or a < b):
                    u_ge = False
                if a is not None and (b is None or b < a):
                    v_ge = False
                if not (u_ge or v_ge):
                    break
            if u_ge:                            # v is dominated by an existing u
                dominated = True
                break
            if not v_ge:                        # u not dominated by v — keep it
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
        # Entry model (off by default → identical to unlimited entries). When on,
        # a lead from a hand declarer is not already in costs an outside entry to
        # that hand; run out and you cannot lead from it. State: which hand you
        # are in ('N'/'S', 'F' = free/on lead in either, 'X' = off lead) and the
        # remaining outside entries to each hand.
        self.entries = False
        self.e0 = ("F", 99, 99)          # root (curr, entries-N, entries-S)

    def _entry(self, curr, eN, eS, lh):
        """Entry cost of leading from ``lh``. Returns the entries left after, or
        None if the hand cannot be reached."""
        if not self.entries or curr == "F" or curr == lh:
            return (eN, eS)
        if lh == "N":
            return (eN - 1, eS) if eN > 0 else None
        return (eN, eS - 1) if eS > 0 else None

    def _tick(self):
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise Timeout

    def solve(self, N, S, wstate, active, curr="F", eN=99, eS=99):
        """Vector set for the position: declarer to lead a fresh trick.
        ``wstate`` is a tuple over all worlds of (E, W) remaining; ``active`` is
        the frozenset of world indices still possible on this line of play.
        ``curr/eN/eS`` are the entry state (ignored unless ``self.entries``)."""
        self._tick()
        if (not N and not S) or not active:
            return [tuple(0 if i in active else None for i in range(self.n))]
        # Inactive worlds are held as None in wstate (nulled the moment they drop
        # out at a defender split), so the key already ignores their stale state.
        # That collapses a huge number of transposition-equivalent positions —
        # what makes the many-world holdings tractable — with an O(1) key.
        key = ((N, S, wstate, active, curr, eN, eS) if self.entries
               else (N, S, wstate, active))
        got = self.memo.get(key)
        if got is not None:
            return got
        miss = set()
        for i in active:
            e, w = wstate[i]
            miss |= set(e) | set(w)
        out = []
        for lh, hand in (("N", N), ("S", S)):          # MAX: union over branches
            ent = self._entry(curr, eN, eS, lh)
            if ent is None:                            # cannot reach this hand
                continue
            nEN, nES = ent
            other = set(S if lh == "N" else N)
            for lc in _reps(hand, other | miss):
                out.extend(self._lead(N, S, wstate, active, lh, lc, nEN, nES))
        out = _pareto(out)
        self.memo[key] = out
        return out

    def _lead(self, N, S, wstate, active, lh, lc, eN=99, eS=99):
        N1, S1 = (_rm(N, lc), S) if lh == "N" else (N, _rm(S, lc))
        _, s2, s3, s4 = (lambda o: [o, _CLOCK[o], _CLOCK[_CLOCK[o]],
                                    _CLOCK[_CLOCK[_CLOCK[o]]]])(lh)
        return self._dmin(N1, S1, wstate, active, s2,
                          lambda ws, ac, c2: self._third(N1, S1, ws, ac, lh, lc,
                                                         s2, c2, s3, s4, eN, eS))

    def _third(self, N1, S1, wstate, active, lh, lc, s2, c2, s3, s4, eN=99, eS=99):
        hand = N1 if s3 == "N" else S1
        if not hand:
            return self._fourth(N1, S1, wstate, active, lh, lc, s2, c2, s3, None,
                                s4, eN, eS)
        miss = set()
        for i in active:
            e, w = wstate[i]
            miss |= set(e) | set(w)
        other = set(S1 if s3 == "N" else N1)
        hi = max([lc] + ([c2] if c2 is not None else []))
        out = []
        for c3 in _reps(hand, other | miss | {hi}):     # MAX: union
            out.extend(self._fourth(N1, S1, wstate, active, lh, lc,
                                    s2, c2, s3, c3, s4, eN, eS))
        return _pareto(out)

    def _fourth(self, N1, S1, wstate, active, lh, lc, s2, c2, s3, c3, s4,
                eN=99, eS=99):
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
                                                           played, s4, c4, eN, eS))

    def _resolve(self, N2, S2, wstate, active, played, s4, c4, eN=99, eS=99):
        trick = dict(played)
        if c4 is not None:
            trick[s4] = c4
        winner = max(trick, key=lambda k: trick[k])
        won = 1 if winner in _NS else 0
        # after the trick declarer is in the hand that won it, or off lead
        curr2 = winner if won else "X"
        sub = self.solve(N2, S2, wstate, active, curr2, eN, eS)
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
            widx = frozenset(plays)
            # Null every world that does not follow this branch: it is inactive
            # from here down, so freezing its state to None canonicalises the
            # position and lets transpositions share a memo entry.
            ws = [wstate[i] if i in widx else None for i in range(self.n)]
            for i, card in plays.items():
                if card is None:
                    continue
                e, w = ws[i]
                ws[i] = (_rm(e, card), w) if dseat == "E" else (e, _rm(w, card))
            rep = None if rid is None else max(plays.values())
            sets.append(cont(tuple(ws), widx, rep))
            keys.append(widx)
        if not sets:
            return [tuple(None for _ in range(self.n))]
        # Combine the branches one at a time (Cartesian product), taking the
        # per-world min, and PARETO-PRUNE after each fold instead of only at the
        # end. Because min is monotone, a partial vector dominated now stays
        # dominated after further folding, so pruning early is exact — and it
        # keeps the intermediate sets small, which is what makes the hard
        # (many-honour) holdings tractable rather than exploding as V^branches.
        rng = range(self.n)
        k0 = keys[0]
        acc = _pareto([tuple(v[i] if i in k0 else None for i in rng)
                       for v in sets[0]])
        for b in range(1, len(sets)):
            kb, sb = keys[b], sets[b]
            new = []
            for j, a in enumerate(acc):
                if j & 63 == 0:            # bound the un-checked work on hard nodes
                    self._tick()
                for bv in sb:
                    new.append(tuple(
                        a[i] if (i not in kb or bv[i] is None)
                        else bv[i] if a[i] is None
                        else (a[i] if a[i] < bv[i] else bv[i])
                        for i in rng))
            # Many (a, bv) combinations collapse to the same vector — dedup before
            # the O(m^2) Pareto pass so it works on distinct vectors only.
            acc = _pareto(list(set(new)), self._tick)
        return acc


def _setup(top, bottom, time_budget, entries=None, start="F", vac=None):
    """Build the shared solver context. ``entries=(eN, eS)`` turns on the entry
    model — outside entries to the top (N) and bottom (S) hands — and ``start`` is
    the hand declarer is on lead in ('N'/'S', or 'F' = either). ``vac=(vW, vE)``
    sets the defenders' vacant spaces (West, East) to skew the odds from the
    bidding; default (None) is unlimited entries and 13/13 vacant spaces — the
    classic result."""
    from .suitplay_opt import _worlds, _runs
    N, S, missing = parse_combo(top, bottom)
    N, S = tuple(sorted(N)), tuple(sorted(S))
    # _worlds takes (vE, vW); the caller gives (vW, vE) — West first, as it reads.
    worlds = _worlds(N, S, missing, (vac[1], vac[0]) if vac else (13, 13))
    n = len(worlds)
    wstate = tuple((e, w) for e, w, _ in worlds)
    weights = [wt for _, _, wt in worlds]
    runid = {}
    for rid, run in enumerate(_runs(missing, set(N) | set(S))):
        for c in run:
            runid[c] = rid
    sv = _Solver(n, runid, deadline=time.monotonic() + time_budget)
    if entries is not None:
        eN, eS = entries
        cap = len(N) + len(S)                 # entries beyond the tricks are moot
        sv.entries = True
        sv.e0 = (start, min(eN, cap), min(eS, cap))
    return N, S, missing, wstate, weights, sv, n


def _root_leads(sv, N, S):
    """The (hand, entries-after) for each hand declarer can afford to lead from
    at the root — respecting the entry state it starts in."""
    curr, eN, eS = sv.e0
    out = []
    for lh in ("N", "S"):
        ent = sv._entry(curr, eN, eS, lh)
        if ent is not None:
            out.append((lh, ent))
    return out


def _score(vecs, weights, n, k):
    """Best strategy's weighted total of worlds taking >= k tricks."""
    return max((sum(weights[i] for i in range(n)
                    if v[i] is not None and v[i] >= k) for v in vecs), default=0)


def _opening_vecs(N, S, missing, wstate, weights, sv, n):
    """Every distinct opening play with its strategy-vector set (computed once —
    scoring it for each trick target afterwards is nearly free)."""
    active = frozenset(range(n))
    miss = set(missing)
    afford = dict(_root_leads(sv, N, S))       # hand -> entries left after leading
    out = []
    for lh, hand in (("N", N), ("S", S)):
        if lh not in afford:                   # cannot reach this hand at the root
            continue
        nEN, nES = afford[lh]
        other = set(S if lh == "N" else N)
        for lc in _reps(hand, other | miss):
            out.append((lh, lc, sv._lead(N, S, wstate, active, lh, lc, nEN, nES)))
    return out


def openings(top: str, bottom: str, goal: int, time_budget: float = 30.0):
    """Each distinct opening play ranked by its exact chance of ``goal`` tricks.
    The best one IS the recommended line — no heuristic guessing."""
    N, S, missing, wstate, weights, sv, n = _setup(top, bottom, time_budget)
    total = sum(weights) or 1
    out = [(100.0 * _score(v, weights, n, goal) / total, lh, lc)
           for lh, lc, v in _opening_vecs(N, S, missing, wstate, weights, sv, n)]
    out.sort(key=lambda x: -x[0])
    return out


def openings_all(top: str, bottom: str, time_budget: float = 30.0, keep: int = 3,
                 ctx=None):
    """{trick target -> [(pct, description), ...]} — the ranked options for EACH
    target, since the best line for 7 tricks need not be the best for 5.

    Only real choices survive: lines scoring 0 or materially worse than the best
    are dropped, as are targets every line makes anyway (nothing to decide there
    — the odds table already says 100%)."""
    N, S, missing, wstate, weights, sv, n = ctx or _setup(top, bottom, time_budget)
    total = sum(weights) or 1
    per_open = [(describe(N, S, lh, lc, missing), v)
                for lh, lc, v in _opening_vecs(N, S, missing, wstate, weights, sv, n)]
    grid = {}
    for k in range(1, len(N) + len(S) + 1):
        best = {}
        for d, vecs in per_open:                # collapse equivalent openings
            p = 100.0 * _score(vecs, weights, n, k) / total
            if d not in best or p > best[d]:
                best[d] = p
        rows = sorted(((p, d) for d, p in best.items()), key=lambda x: -x[0])
        if not rows or rows[0][0] < 0.05:
            continue
        topp = rows[0][0]
        if topp >= 99.95 and rows[-1][0] >= 99.95:
            continue                            # every line makes it — no decision
        rows = [(p, d) for p, d in rows
                if p >= 0.05 and p >= topp * 0.5][:keep]
        if rows:
            grid[k] = rows
    return grid


def equiv_all(cum, ctx):
    """{target -> [equivalent opening descriptions]} for targets where more than
    one genuinely different line gives the SAME best chance — so the note can say
    'either of these', rather than implying the first is uniquely right."""
    N, S, missing, wstate, weights, sv, n = ctx
    total = sum(weights) or 1
    per_open = [(describe(N, S, lh, lc, missing), v)
                for lh, lc, v in _opening_vecs(N, S, missing, wstate, weights, sv, n)]
    out = {}
    for k in cum:
        if cum[k] >= 99.95:
            continue
        best = {}
        for d, vecs in per_open:
            p = 100.0 * _score(vecs, weights, n, k) / total
            best[d] = max(best.get(d, 0.0), p)
        if not best:
            continue
        top = max(best.values())
        ties = sorted(d for d, p in best.items() if abs(p - top) < 0.05)
        if top >= 0.05 and len(ties) > 1:
            out[k] = ties
    return out


def describe(N, S, lh, lc, missing):
    """Short label for an opening, by its ROLE — so equivalent low cards (the 4
    and the 6 from one hand) read the same and don't split a tie into two
    spurious entries. Kept terse: these sit in a scannable table."""
    other = S if lh == "N" else N
    ms = sorted(missing)
    if not any(m > lc for m in ms):                  # nothing out beats it
        return f"Cash the {VALRANK[lc]}"
    tenace = sorted((c for c in other if c >= 10), reverse=True)
    # only a genuinely low card is "low to the honours" — leading an honour is a
    # different play entirely (an unblock or a card led to be covered)
    if lc < 10 and tenace and any(m < max(tenace) for m in ms):
        return "Low to the " + "".join(VALRANK[c] for c in tenace)
    return f"Lead the {VALRANK[lc]}"


def _defender_branches(sv, N_, S_, wstate, active, dseat):
    """The defender's distinct plays: run id -> ({world: card}, new wstate)."""
    out = {}
    for i in active:
        e, w = wstate[i]
        dc = e if dseat == "E" else w
        if not dc:
            out.setdefault(None, {})[i] = None
            continue
        for rid in {sv.runid[c] for c in dc}:
            card = min(c for c in dc if sv.runid[c] == rid)
            out.setdefault(rid, {})[i] = card
    return out


def _apply(wstate, plays, dseat):
    ws = list(wstate)
    for i, card in plays.items():
        if card is None:
            continue
        e, w = ws[i]
        ws[i] = (_rm(e, card), w) if dseat == "E" else (e, _rm(w, card))
    return tuple(ws)


def _missing(wstate, active):
    m = set()
    for i in active:
        e, w = wstate[i]
        m |= set(e) | set(w)
    return m


def _score_act(vecs, weights, active, need):
    """Best strategy's weighted worlds (within ``active``) taking >= ``need``."""
    return max((sum(weights[i] for i in active
                    if v[i] is not None and v[i] >= need) for v in vecs),
               default=0)


def _score_ev(vecs, weights, active):
    """Best strategy's weighted EXPECTED trick count (within ``active``) — the
    matchpoint objective: play for the average, not for a particular target."""
    return max((sum(weights[i] * v[i] for i in active if v[i] is not None)
                for v in vecs), default=0)


def _winseat(lh, lc, s2, c2, s3, c3, s4, c4):
    trick = {lh: lc}
    for s, c in ((s2, c2), (s3, c3), (s4, c4)):
        if c is not None:
            trick[s] = c
    return max(trick, key=lambda k: trick[k])


def _won(lh, lc, s2, c2, s3, c3, s4, c4):
    return _winseat(lh, lc, s2, c2, s3, c3, s4, c4) in _NS


def _plan_tree(sv, N, S, wstate, active, need, weights, n,
               curr="F", eN=99, eS=99, ev=False, depth=0):
    """Declarer's OPTIMAL CONDITIONAL plan as a tree, for one of two objectives:
    reach ``need`` tricks (IMPs / a contract), or — with ``ev=True`` — maximise the
    expected number of tricks (matchpoints).

    A real suit-combination line is conditional — "finesse the jack; if the king
    appears win the ace, otherwise lead the queen" — and a single flat sequence
    cannot say that. So we extract the strategy the solver actually found: at each
    of declarer's choices (which card to lead, which to play third) take the play
    that maximises the objective, and at the defender's second-hand play BRANCH on
    what shows (keyed by equivalence run — the information declarer really has).
    Each branch carries its own third-hand response and its own continuation,
    which is where the "if the king appears" split comes from."""
    if depth >= 8 or not active or (not ev and need <= 0) or (not N and not S):
        return None

    def _sc(vs, act):
        return _score_ev(vs, weights, act) if ev \
            else _score_act(vs, weights, act, need)

    miss = _missing(wstate, active)
    # MAX: declarer's lead — the affordable one that best serves the objective.
    best = None
    for lh, hand in (("N", N), ("S", S)):
        ent = sv._entry(curr, eN, eS, lh)
        if ent is None:                        # cannot reach this hand — skip
            continue
        pEN, pES = ent
        other = set(S if lh == "N" else N)
        for lc in _reps(hand, other | miss):
            sc = _sc(sv._lead(N, S, wstate, active, lh, lc, pEN, pES), active)
            if best is None or sc > best[0]:
                best = (sc, lh, lc, pEN, pES)
    if best is None:
        return None
    _, lh, lc, eN, eS = best                    # entries left after paying to lead
    N1, S1 = (_rm(N, lc), S) if lh == "N" else (N, _rm(S, lc))
    s2, s3, s4 = _CLOCK[lh], _CLOCK[_CLOCK[lh]], _CLOCK[_CLOCK[_CLOCK[lh]]]
    hand3 = N1 if s3 == "N" else S1
    branches = []
    for rid2, plays2 in _defender_branches(sv, N1, S1, wstate, active, s2).items():
        ws2, act2 = _apply(wstate, plays2, s2), frozenset(plays2)
        c2 = max((c for c in plays2.values() if c is not None), default=None)
        # MAX: declarer's third-hand response to THIS observed second-hand card.
        c3 = None
        if hand3:
            miss3 = _missing(ws2, act2)
            other3 = set(S1 if s3 == "N" else N1)
            hi = max([lc] + ([c2] if c2 is not None else []))
            b3 = None
            for cand in _reps(hand3, other3 | miss3 | {hi}):
                sc = _sc(sv._fourth(N1, S1, ws2, act2, lh, lc, s2, c2,
                                    s3, cand, s4, eN, eS), act2)
                if b3 is None or sc > b3[0]:
                    b3 = (sc, cand)
            c3 = b3[1] if b3 else None
        N2, S2 = N1, S1
        if c3 is not None:
            N2, S2 = (_rm(N1, c3), S1) if s3 == "N" else (N1, _rm(S1, c3))
        # MIN: fourth hand plays best defence — the reply leaving declarer least.
        br4 = _defender_branches(sv, N2, S2, ws2, act2, s4)

        def _after(r):
            p4 = br4[r]
            c4r = max((c for c in p4.values() if c is not None), default=None)
            seat = _winseat(lh, lc, s2, c2, s3, c3, s4, c4r)
            cr = seat if seat in _NS else "X"
            return sv.solve(N2, S2, _apply(ws2, p4, s4), frozenset(p4),
                            cr, eN, eS)
        rid4 = min(br4, key=lambda r: _sc(_after(r), frozenset(br4[r])))
        plays4 = br4[rid4]
        ws3, act3 = _apply(ws2, plays4, s4), frozenset(plays4)
        c4 = max((c for c in plays4.values() if c is not None), default=None)
        seat = _winseat(lh, lc, s2, c2, s3, c3, s4, c4)
        won = seat in _NS
        curr2 = seat if won else "X"
        cont = _plan_tree(sv, N2, S2, ws3, act3, need - (1 if won else 0),
                          weights, n, curr2, eN, eS, ev, depth + 1)
        branches.append({"c2": c2, "c3": c3, "won": won,
                         "wt": sum(weights[i] for i in act2), "cont": cont})
    return {"lc": lc, "lh": lh, "s3": s3, "out": frozenset(miss),
            "hand3": hand3, "branches": branches}


def _after4(N2, S2, ws2, plays4, s4):
    return N2, S2, _apply(ws2, plays4, s4), frozenset(plays4)


def _principal_from(ctx, goal, max_tricks=6):
    """Replay declarer's plan for ``goal`` tricks, reusing an existing solver.

    Re-solving each sub-position is optimal (the information set is Markovian),
    so greedily taking the best play at each step follows a genuinely optimal
    strategy. Crucially we trace only the worlds where declarer *achieves* the
    goal: a 24% goal is missed in the other 76%, and following the (more likely)
    failing defence would show declarer giving up — "duck a round" for a play
    that is really a double finesse. Restricting to the winning worlds shows the
    line you take WHEN it works, which is what a suit-combination note describes."""
    N, S, missing, wstate, weights, sv, n = ctx
    # Describe the line the way a suit-combination note does: in ONE favourable
    # layout. Pick the most likely world in which the best strategy makes the
    # goal, and trace declarer's play there. With a single world declarer sees
    # the cards (double-dummy), so a finesse is unambiguous — no tie-breaking
    # between "cash" and "finesse", and no failing-defence "duck a round" for a
    # play that is really a finesse.
    vecs = sv.solve(N, S, wstate, frozenset(range(n)))
    best_vec = max(vecs, key=lambda v: sum(
        weights[i] for i in range(n) if v[i] is not None and v[i] >= goal))
    winners = [i for i in range(n)
               if best_vec[i] is not None and best_vec[i] >= goal]
    if winners:
        world = max(winners, key=lambda i: weights[i])
        active = frozenset([world])
    else:
        active = frozenset(range(n))

    def reply(branches):
        return max(branches, key=lambda r: sum(weights[i] for i in branches[r]))

    need = goal
    steps = []
    for _ in range(max_tricks):
        if (not N and not S) or not active or need <= 0:
            break
        miss = set()
        for i in active:
            e, w = wstate[i]
            miss |= set(e) | set(w)
        best = None
        for lh, hand in (("N", N), ("S", S)):
            other = set(S if lh == "N" else N)
            for lc in _reps(hand, other | miss):
                sc = _score(sv._lead(N, S, wstate, active, lh, lc),
                            weights, n, need)
                if best is None or sc > best[0]:
                    best = (sc, lh, lc)
        if best is None:
            break
        _, lh, lc = best
        N1, S1 = (_rm(N, lc), S) if lh == "N" else (N, _rm(S, lc))
        _, s2, s3, s4 = (lambda o: [o, _CLOCK[o], _CLOCK[_CLOCK[o]],
                                    _CLOCK[_CLOCK[_CLOCK[o]]]])(lh)
        br = _defender_branches(sv, N1, S1, wstate, active, s2)
        rid = reply(br)
        plays = br[rid]
        ws2, act2 = _apply(wstate, plays, s2), frozenset(plays)
        c2 = max((c for c in plays.values() if c is not None), default=None)
        # declarer's card in the other hand — this is where a finesse happens
        hand3 = N1 if s3 == "N" else S1
        c3 = None
        if hand3:
            miss3 = set()
            for i in act2:
                e, w = ws2[i]
                miss3 |= set(e) | set(w)
            other3 = set(S1 if s3 == "N" else N1)
            hi = max([lc] + ([c2] if c2 is not None else []))
            bc = None
            for cand in _reps(hand3, other3 | miss3 | {hi}):
                sc = _score(sv._fourth(N1, S1, ws2, act2, lh, lc, s2, c2,
                                       s3, cand, s4), weights, n, need)
                if bc is None or sc > bc[0]:
                    bc = (sc, cand)
            c3 = bc[1] if bc else None
        step = {"lead": lc, "lead_hand": hand, "other": c3,
                "other_hand": hand3, "out": frozenset(miss)}
        steps.append(step)
        N2, S2 = N1, S1
        if c3 is not None:
            N2, S2 = (_rm(N1, c3), S1) if s3 == "N" else (N1, _rm(S1, c3))
        br4 = _defender_branches(sv, N2, S2, ws2, act2, s4)
        rid4 = reply(br4)
        plays4 = br4[rid4]
        ws3, act3 = _apply(ws2, plays4, s4), frozenset(plays4)
        c4 = max((c for c in plays4.values() if c is not None), default=None)
        trick = {lh: lc}
        if c2 is not None:
            trick[s2] = c2
        if c3 is not None:
            trick[s3] = c3
        if c4 is not None:
            trick[s4] = c4
        step["won"] = max(trick, key=lambda k: trick[k]) in _NS   # declarer won?
        if step["won"]:
            need -= 1
        N, S, wstate, active = N2, S2, ws3, act3
    return steps


def principal_line(top: str, bottom: str, goal: int, time_budget: float = 30.0):
    return _principal_from(_setup(top, bottom, time_budget), goal)


def plans_all(top: str, bottom: str, cum: dict, time_budget: float = 30.0,
              ctx=None):
    """{target -> plan text}. The plan for 4 tricks is often NOT the plan for 3 —
    playing for the maximum can be a 3% shot you'd never take — so every target
    the odds table shows gets its own line. One solver is shared across targets."""
    ctx = ctx or _setup(top, bottom, time_budget)
    out = {}
    for k in sorted(cum, reverse=True):
        if cum[k] >= 99.95:
            continue          # every line makes it — naming one would imply a choice
        try:
            out[k] = describe_plan(ctx, k)
        except Timeout:
            break             # keep whatever we already have
    return out


def _classify(st):
    """Name one trick of the plan, in the paper's vocabulary: cash (a card that
    cannot lose), finesse (lead low toward a card that a higher one might beat),
    duck (deliberately play low from both hands), else just name the card."""
    lc, c3, out = st["lead"], st["other"], st["out"]
    hi = lc if c3 is None else max(lc, c3)
    over = [m for m in out if m > hi]
    # leading an honour and beating it with a higher one from the other hand is
    # an unblock — saying "cash the A" hides the card you must lead to get it
    if c3 is not None and lc >= 10 and c3 > lc:
        return f"overtake the {VALRANK[lc]} with the {VALRANK[c3]}"
    if not over:                                  # a sure winner
        return f"cash the {VALRANK[hi]}" if hi >= 10 else ""   # spots just run
    # A duck DELIBERATELY LOSES a trick: low from both hands AND declarer loses
    # it. If the defenders duck and declarer's low card wins, that is not a duck.
    if (c3 is not None and not st.get("won", True)
            and lc == min(st["lead_hand"]) and c3 == min(st["other_hand"])):
        return "duck a round"
    # A finesse leads low toward a near-honour, hoping a specific higher card
    # sits favourably. The card must be worth it (>= 9) with something higher out.
    if (c3 is not None and lc < c3 and c3 >= 9 and any(m > c3 for m in out)):
        return f"lead low and finesse the {VALRANK[c3]}"
    return f"lead the {VALRANK[hi]}" if hi >= 10 else ""   # a low card — running


def render_plan(steps):
    """'Cash the K, then lead low and finesse the J.' Cashing spot cards at the
    end is just running the suit, not a decision, so it is left out."""
    out, prev = [], None
    for st in steps:
        d = _classify(st)
        if d and d != prev:                       # skip empties, collapse repeats
            out.append(d)
            prev = d
    if not out:
        return ""
    txt = out[0][0].upper() + out[0][1:]
    if len(out) > 1:
        txt += ", then " + ", then ".join(out[1:])
    return txt + "."


def _lead_desc(lc, hand3, out):
    """How declarer starts the trick, when the second-hand play will branch."""
    if lc >= 10:
        return f"lead the {VALRANK[lc]}"
    tenace = sorted((c for c in hand3 if c >= 9), reverse=True)
    if tenace and any(m < tenace[0] for m in out):
        return "lead low toward the " + "".join(VALRANK[c] for c in tenace[:3])
    return "lead low"


def _resp_desc(lc, c3, out, won):
    """Declarer's third-hand play, as a response to what second hand showed."""
    if c3 is None:
        return ""
    if lc >= 10 and c3 > lc:
        return f"overtake with the {VALRANK[c3]}"
    if lc < c3 and c3 >= 9 and any(m > c3 for m in out):
        return f"finesse the {VALRANK[c3]}"
    if not any(m > c3 for m in out):
        return f"win the {VALRANK[c3]}" if c3 >= 10 else ""
    return f"play the {VALRANK[c3]}" if c3 >= 10 else ""


def _combined(lc, c3, hand3, out, won):
    """One phrase for declarer's play this trick, when second hand's card does not
    change it: cash / finesse (led low toward a card, or an honour run past a gap)
    / duck / a plain lead."""
    hi = lc if c3 is None else max(lc, c3)
    over = [m for m in out if m > hi]
    if c3 is not None and lc >= 10 and c3 > lc:
        return f"overtake the {VALRANK[lc]} with the {VALRANK[c3]}"
    # finesse by leading low toward a higher card that a missing card can beat
    if c3 is not None and lc < c3 and c3 >= 9 and any(m > c3 for m in out):
        return f"lead low and finesse the {VALRANK[c3]}"
    if not over:
        return f"cash the {VALRANK[hi]}" if hi >= 10 else ""   # spots just run
    # finesse by running an honour: you lead it meaning to let it ride, because a
    # missing card can still beat it and you hope it sits favourably. This needs a
    # real two-hand tenace — the partner hand must follow with a low card. With
    # the partner void (c3 is None) there is no finesse: both defenders play after
    # you, so a higher card always beats it — you are just cashing/running the suit
    # from the top and hoping the missing honour falls.
    if lc >= 9 and c3 is not None and c3 < lc and any(m > lc for m in out):
        return f"run the {VALRANK[lc]}"
    if (c3 is not None and not won
            and lc == min([lc, c3]) and lc < 9 and c3 < 9):
        return "duck a round"
    return f"lead the {VALRANK[hi]}" if hi >= 10 else ""


def _join(parts):
    return ", then ".join(p for p in parts if p)


def _all_winners(node):
    """A raw node whose whole subtree is just cashing established winners — no
    finesse or duck anywhere below. Such a tail is 'draw the rest', not a
    decision, so it is collapsed to a single leaf."""
    if node is None:
        return True
    for br in node["branches"]:
        c3, lc, out = br["c3"], node["lc"], node["out"]
        hi = lc if c3 is None else max(lc, c3)
        if any(m > hi for m in out):          # something can still beat us — real
            return False
        if not _all_winners(br["cont"]):
            return False
    return True


def _cap(s):
    return s[0].upper() + s[1:] if s else s


def _to_display(node, depth=0):
    """Turn the raw strategy tree into a clean, drillable display tree the way a
    suit-combination note reads: a MAIN LINE that flows, with honour-appears cases
    hung off it as drillable exceptions.

    Node: ``{"action", "notes": [{"cond", "node"}], "next"}``. ``next`` is the
    main continuation (defender played low — declarer stays blind, so the finesse
    or drop shows); ``notes`` are the 'if the king appears' side lines."""
    if node is None or depth > 10:
        return None
    if _all_winners(node):
        return {"action": "Draw the rest", "notes": [], "next": None}
    lc, hand3, out = node["lc"], node["hand3"], node["out"]
    notes, main_pool = [], []
    for br in node["branches"]:
        if br["c2"] is not None and br["c2"] >= 11:      # an honour showed
            resp = _resp_desc(lc, br["c3"], out, br["won"])
            cont = _to_display(br["cont"], depth + 1)
            # if declarer just follows low, the note is really about what happens
            # next — show that directly rather than an empty "play low" line
            sub = ({"action": _cap(resp), "notes": [], "next": cont}
                   if resp else cont)
            if sub and not _trivial(sub):    # skip gifts: honour drops, we claim
                notes.append((br["c2"], sub))
        else:
            main_pool.append(br)
    main = max(main_pool or node["branches"], key=lambda b: b["wt"])
    action = _combined(lc, main["c3"], hand3, out, main["won"])
    nxt = _to_display(main["cont"], depth + 1)
    seen, note_list = set(), []                          # one note per honour
    for c2, nd in sorted(notes, key=lambda x: -x[0]):
        if c2 in seen:
            continue
        seen.add(c2)
        note_list.append({"cond": f"if the {VALRANK[c2]} appears", "node": nd})
    if not action:                    # declarer just follows low — nothing to say
        return nxt                    # its own (trivial) exceptions carry no info
    return {"action": _cap(action), "notes": note_list, "next": nxt}


def _trivial(nd):
    """A note that carries no decision — declarer just follows and cashes out.
    An honour dropping under a winner is a gift, not a line worth spelling."""
    if nd is None:
        return True
    if nd["action"] in ("", "Follow low") and not nd["notes"]:
        return _trivial(nd["next"])
    if nd["action"] == "Draw the rest" and not nd["notes"]:
        return True
    return False


def _cr(c):
    """A card for display: its rank if it matters (>= 9), else a low spot ``x``."""
    return VALRANK[c] if c >= 9 else "x"


def _plays(lh, lc, s3, c3):
    """Declarer's meaningful cards this trick as {hand, rank}: the lead, plus a
    card played OVER it from the other hand (a finesse or unblock). A lower follow
    card is just following suit and is left out. Hand 1 = top (N), 2 = bottom."""
    out = [{"h": 1 if lh == "N" else 2, "r": _cr(lc)}]
    if c3 is not None and c3 > lc:
        out.append({"h": 1 if s3 == "N" else 2, "r": _cr(c3)})
    return out


def _is_low_lead(plays):
    """A single low-spot lead — declarer just running the suit, not a decision."""
    return len(plays) == 1 and plays[0]["r"] == "x"


def _trivial_cards(nd):
    """A card note that carries no decision — declarer just follows and cashes."""
    if nd is None:
        return True
    if nd.get("draw"):
        return True
    if not nd.get("plays") and not nd.get("notes"):
        return _trivial_cards(nd.get("next"))
    return False


def _to_cards(node, depth=0, shown=frozenset()):
    """Declarer's optimal conditional line as a drillable tree of the ACTUAL CARDS
    played — no prose classification (which is where the wording went wrong). Same
    shape as the word tree: a main line that flows, honour-appears cases hung off
    it. Each node: ``{"plays": [{h, r}], "win", "notes": [{show, node}], "next"}``;
    a leaf ``{"draw": True}`` means cash the established winners. ``shown`` carries
    the honours already noted down the main line so a repeated finesse does not
    repeat "if the king appears" on every round."""
    if node is None or depth >= 9:
        return None
    if _all_winners(node):
        return {"draw": True}
    lc, lh, s3 = node["lc"], node["lh"], node["s3"]
    honours, main_pool = [], []
    for br in node["branches"]:
        (honours if br["c2"] is not None and br["c2"] >= 11
         else main_pool).append(br)
    main = max(main_pool or node["branches"], key=lambda b: b["wt"])
    seen, notes = set(), []
    for br in sorted(honours, key=lambda b: -b["c2"]):
        c2, c3 = br["c2"], br["c3"]
        # The note matters only when declarer COVERS the honour — plays a higher
        # card over it (e.g. "if the king appears, win the ace"). An honour that
        # simply wins a trick for the defence, falls under a card already led, or
        # was already noted earlier on the main line, is not a fresh decision.
        r = VALRANK[c2]
        if c2 in seen or r in shown or c3 is None or c3 <= c2:
            continue
        seen.add(c2)
        sub = {"plays": [{"h": 1 if s3 == "N" else 2, "r": _cr(c3)}],
               "win": br["won"], "notes": [],
               "next": _to_cards(br["cont"], depth + 1)}
        notes.append({"show": r, "node": sub})
    nxt = _to_cards(main["cont"], depth + 1,
                    shown | {n["show"] for n in notes})
    return {"plays": _plays(lh, lc, s3, main["c3"]), "win": main["won"],
            "notes": notes, "next": nxt}


def card_tree(ctx, goal, ev=False):
    """The optimal line for ``goal`` tricks (or the matchpoint line if ``ev``) as a
    drillable tree of the cards actually played."""
    N, S, missing, wstate, weights, sv, n = ctx
    raw = _plan_tree(sv, N, S, wstate, frozenset(range(n)), 0 if ev else goal,
                     weights, n, *sv.e0, ev=ev)
    return _to_cards(raw)


def card_headline(tree):
    """The main line down the spine, as a flat list of the tricks' card plays —
    the collapsed view for the odds table. Pure low-card leads (running the suit)
    are left off, as they were in the prose version."""
    spine, node, seen = [], tree, 0
    while node is not None and seen < 9:
        if node.get("draw"):
            break
        if node.get("plays") and not _is_low_lead(node["plays"]):
            spine.append(node["plays"])
        node = node.get("next")
        seen += 1
    return spine


def plan_tree(ctx, goal):
    """The optimal conditional line for ``goal`` tricks, as a drillable tree."""
    N, S, missing, wstate, weights, sv, n = ctx
    raw = _plan_tree(sv, N, S, wstate, frozenset(range(n)), goal, weights, n,
                     *sv.e0)
    return _to_display(raw)


def matchpoints(ctx):
    """The matchpoint line — maximise the expected (average) number of tricks —
    with its average, its drillable tree, and any lines that tie it.

    At matchpoints you are beating the field, not making a contract, so over- and
    under-tricks all count: the standard suit-combination objective is the highest
    average. It often differs from the target line (chasing the maximum can cost
    most of a trick on average), so it earns its own row."""
    N, S, missing, wstate, weights, sv, n = ctx
    total = sum(weights) or 1
    active = frozenset(range(n))
    ops = [(describe(N, S, lh, lc, missing),
            _score_ev(v, weights, active) / total)
           for lh, lc, v in _opening_vecs(N, S, missing, wstate, weights, sv, n)]
    best = {}                                  # collapse equivalent openings
    for d, ev in ops:
        if d not in best or ev > best[d]:
            best[d] = ev
    ranked = sorted(best.items(), key=lambda x: -x[1])
    top = ranked[0][1] if ranked else 0.0
    ties = [d for d, ev in ranked if abs(ev - top) < 5e-4]
    tree = card_tree(ctx, 0, ev=True)
    return {"tricks": round(top, 2), "tree": tree,
            "plan": card_headline(tree), "equiv": ties}


def _fmt_hand(cards):
    """A defender holding for the split table: honours by name, spots as ``x``
    (they are interchangeable), e.g. {K,4,3} -> 'Kxx'. Void shows as an em dash."""
    honours = "".join(VALRANK[c] for c in sorted(cards, reverse=True) if c >= 10)
    spots = sum(1 for c in cards if c < 10)
    return (honours + "x" * spots) or "—"


def split_table(ctx, vecs):
    """How the recommended (matchpoints) line pays off by defender split — the
    SuitPlay-style breakdown: for one fixed best line, every layout's a-priori
    probability and the tricks it yields against best defence. Rows are the split
    (West/East holdings), grouped where the spots don't distinguish them."""
    N, S, missing, wstate, weights, sv, n = ctx
    total = sum(weights) or 1
    if not vecs:
        return []
    # The line that maximises average tricks — a single, well-defined strategy;
    # its per-world vector is exactly "tricks in each layout".
    best = max(vecs, key=lambda v: sum(weights[i] * v[i] for i in range(n)
                                       if v[i] is not None))
    agg = {}                                    # (west, east, lw, le, tricks) -> wt
    for i in range(n):
        t = best[i]
        if t is None:
            continue
        e, w = wstate[i]
        key = (_fmt_hand(w), _fmt_hand(e), len(w), len(e), t)
        agg[key] = agg.get(key, 0) + weights[i]
    rows = [{"west": k[0], "east": k[1], "break": f"{k[2]}-{k[3]}",
             "tricks": k[4], "prob": round(100.0 * wt / total, 1)}
            for k, wt in agg.items()]
    rows.sort(key=lambda r: (-r["tricks"], -r["prob"]))
    # Store canonically: West is the defender behind the bottom hand, which the
    # N/S-normalising cache can flip. Emit as-if canonical so _display can put it
    # back to the actual holding's orientation (see _flip_splits).
    from .suitcache import _owner_rle
    if _owner_rle(set(N), set(S)) > _owner_rle(set(S), set(N)):
        rows = _flip_splits(rows)
    return rows


def _flip_splits(rows):
    """Swap the West/East view of a split table (used to re-orient it)."""
    out = []
    for r in rows:
        lw, le = r["break"].split("-")
        out.append({**r, "west": r["east"], "east": r["west"],
                    "break": f"{le}-{lw}"})
    return out


def _headline(disp):
    """One-line summary: the main line down the spine (ignoring the exceptions).
    'Draw the rest' is just cashing winners, so it is left off the headline."""
    parts, node, seen = [], disp, 0
    while node is not None and seen < 8:
        if node["action"] and node["action"] != "Draw the rest":
            parts.append(node["action"])
        node = node["next"]
        seen += 1
    if not parts:
        return ""
    parts = [parts[0]] + [p[0].lower() + p[1:] for p in parts[1:]]
    txt = _join(parts)
    return txt[0].upper() + txt[1:] + "."


def describe_plan(ctx, goal):
    return _headline(plan_tree(ctx, goal))


def trees_all(cum, ctx):
    """{target -> drillable CARD tree}. Only targets that involve a real choice
    (not the ones every line makes) get a tree."""
    out = {}
    for k in sorted(cum, reverse=True):
        if cum[k] >= 99.95:
            continue
        try:
            t = card_tree(ctx, k)
        except Timeout:
            break
        if t:
            out[k] = t
    return out


def plays_all(trees):
    """{target -> card headline} derived from the card trees (the collapsed view
    for the odds table)."""
    return {k: card_headline(t) for k, t in trees.items()}


def _display(top, bottom, payload):
    """Attach display fields, derived from the ACTUAL holding, to a cached (or
    fresh) canonical-invariant payload."""
    N, S, missing = parse_combo(top, bottom)
    mt = payload.get("max_tricks", 0)
    plans, grid = payload.get("plans") or {}, payload.get("grid") or {}
    trees = payload.get("trees") or {}
    # The split table is stored canonically; put its West/East back to how THIS
    # holding sits (West behind the bottom hand).
    from .suitcache import _owner_rle
    splits = payload.get("splits") or []
    if splits and _owner_rle(set(N), set(S)) > _owner_rle(set(S), set(N)):
        splits = _flip_splits(splits)
    payload.update(
        top="".join(VALRANK[r] for r in sorted(N, reverse=True)),
        bottom="".join(VALRANK[r] for r in sorted(S, reverse=True)),
        missing="".join(VALRANK[r] for r in sorted(missing, reverse=True)) or "—",
        exact=True, play=plans.get(mt, ""), lines=grid.get(mt, []),
        tree=trees.get(mt), equiv=payload.get("equiv") or {},
        mp=payload.get("mp"), splits=splits)
    return payload


import collections as _collections
import math as _math
import threading as _threading

# Vacant spaces only reweight the worlds; the solved vector sets never change.
# So the SLOW part (the solve) is cached in memory per (holding, entries, start)
# and a different vacant-space setting just re-scores it — turning a ~1s re-solve
# into ~50ms. Bounded so long sessions do not grow without limit.
_BASE = _collections.OrderedDict()
_BASE_LOCK = _threading.Lock()
_BASE_MAX = 16


def _world_weights(wstate, runs, m, vE, vW):
    """A-priori weights for the given vacant spaces, reconstructed from each
    world's West holding (so a solved set of worlds can be reweighted without
    re-solving). A world impossible under these vacant spaces gets weight 0."""
    tot = vE + vW
    out = []
    for e, w in wstate:
        wc, ec = len(w), len(e)
        if wc > vW or ec > vE:                 # not enough room this side
            out.append(0)
            continue
        mult = 1
        for run in runs:
            k = sum(1 for c in w if c in run)
            if k:
                mult *= _math.comb(len(run), k)
        out.append(mult * _math.comb(tot - m, vW - wc))
    return out


def _base_solve(top, bottom, time_budget, entries, start):
    """Solve (or reuse) the vector sets for a holding under an entry setting, with
    neutral 13/13 weights. Cached in memory: reweighting for vacant spaces reuses
    it. Returns (ctx, vecs, runs)."""
    key = (top, bottom, entries, start)
    with _BASE_LOCK:
        got = _BASE.get(key)
        if got is not None:
            _BASE.move_to_end(key)
    if got is not None:
        ctx, vecs, runs = got
        ctx[5].deadline = time.monotonic() + time_budget   # refresh sv deadline
        return ctx, vecs, runs
    from .suitplay_opt import _runs
    ctx = _setup(top, bottom, time_budget, entries=entries, start=start, vac=None)
    N, S, missing, wstate, weights, sv, n = ctx
    vecs = sv.solve(N, S, wstate, frozenset(range(n)), *sv.e0)
    runs = tuple(_runs(missing, set(N) | set(S)))
    with _BASE_LOCK:
        _BASE[key] = (ctx, vecs, runs)
        while len(_BASE) > _BASE_MAX:
            _BASE.popitem(last=False)
    return ctx, vecs, runs


def suit_vec(top: str, bottom: str, time_budget: float = 30.0,
             use_cache: bool = True, entries=None, start: str = "F",
             vac=None) -> dict:
    """Exact trick-count distribution by vector propagation, plus the real line.
    A solved holding is cached — the answer never changes — so it is instant next
    time (and the precompute fills the slow tail offline).

    ``entries=(eN, eS)`` constrains outside entries to the top (N) and bottom (S)
    hands (``start`` = the hand on lead, 'N'/'S'/'F'). ``vac=(vW, vE)`` sets the
    defenders' vacant spaces to skew the odds from the bidding. Neither is stored
    in the on-disk cache — that holds the plain unlimited/13-13 answer only — but
    the solve is reused in memory, so changing vacant spaces re-scores instantly."""
    custom = entries is not None or vac is not None
    if use_cache and not custom:
        try:
            from .suitcache import get_full
            hit = get_full(top, bottom)
        except Exception:      # noqa: BLE001
            hit = None
        if hit:
            hit["cached"] = True
            return _display(top, bottom, hit)

    ctx, vecs, runs = _base_solve(top, bottom, time_budget, entries, start)
    N, S, missing, wstate, weights0, sv, n = ctx
    weights = (weights0 if vac is None
               else _world_weights(wstate, runs, len(missing), vac[1], vac[0]))
    if vac is not None and 0 in weights:
        # These vacant spaces make some layouts impossible. The plan tree must not
        # even mention them, which the reused all-worlds solve would — so drop back
        # to an exact solve over the reduced world set (rare; extreme skews only).
        ctx = _setup(top, bottom, time_budget, entries=entries, start=start,
                     vac=vac)
        N, S, missing, wstate, weights, sv, n = ctx
        vecs = sv.solve(N, S, wstate, frozenset(range(n)), *sv.e0)
    else:
        ctx = (N, S, missing, wstate, weights, sv, n)
    total = sum(weights) or 1
    maxt = len(N) + len(S)
    cum = {}
    for k in range(1, maxt + 1):
        p = 100.0 * _score(vecs, weights, n, k) / total
        if p < 0.05:
            break
        cum[k] = p
    # The odds are the answer and are computed exactly; the plans and line grid
    # are commentary. On a slow holding let the commentary time out rather than
    # lose the exact odds with it — and don't cache a half-built result.
    grid, plans, trees, equiv, mp, splits = {}, {}, {}, {}, None, []
    complete = True
    if cum:
        try:
            grid = openings_all(top, bottom, time_budget, ctx=ctx)
            trees = trees_all(cum, ctx)          # drillable CARD trees
            plans = plays_all(trees)             # collapsed card headlines
            equiv = equiv_all(cum, ctx)
            mp = matchpoints(ctx)
            splits = split_table(ctx, vecs)
        except Timeout:
            complete = False

    r = _display(top, bottom, {"worlds": n, "strategies": len(vecs), "cum": cum,
                               "max_tricks": max(cum) if cum else 0,
                               "plans": plans, "grid": grid, "trees": trees,
                               "equiv": equiv, "mp": mp, "splits": splits})
    if use_cache and complete and not custom:
        try:
            from .suitcache import put_full
            put_full(top, bottom, r)
        except Exception:      # noqa: BLE001
            pass
    return r
