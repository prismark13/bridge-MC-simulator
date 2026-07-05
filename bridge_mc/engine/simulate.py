"""The Monte-Carlo loop: sample deals, DD-solve in batches, aggregate.

UI-agnostic. Cancellation and progress are injected as callbacks, so the same
function backs the Qt worker and the headless CLI.
"""
from __future__ import annotations

import random

from redeal import Contract

from ..domain.contracts import (
    ALL_CS, ATTR, GAMES, ORDER, SIDE_IDX, SLAMS, SUIT_SYM, to_imps)
from ..domain.types import Breakdown, ContractStat, SampleDeal, SimResult
from .sampling import build_dealer, fmt_hand, smart_seat
from .solver import BATCH, default_solver

_STORE_CAP = 100_000     # cap for keeping per-deal score vectors (IMP swing)
_SUIT_IDX = {"S": 0, "H": 1, "D": 2, "C": 3}
_NEED = {lab: need for lab, _s, need, _cs in GAMES + SLAMS}


def _breakdown(seat, contract_label, hcps, shapes, sts):
    """Slice the decision contract's make-rate by the focus seat's
    HCP / trump length / shortness."""
    strain = "N" if contract_label.endswith("NT") else contract_label[-1]
    need = _NEED[contract_label]

    def cs(label, idxs):
        m = sum(1 for i in idxs if sts[i][strain] >= need)
        return ContractStat(label, m, len(idxs))

    rng = range(len(hcps))
    by_hcp = [cs(f"{h} HCP", [i for i in rng if hcps[i] == h])
              for h in sorted(set(hcps))]
    by_short = []
    for lab, test in (("singleton/void", lambda s: min(s) <= 1),
                      ("doubleton", lambda s: min(s) == 2),
                      ("no short suit", lambda s: min(s) >= 3)):
        idxs = [i for i in rng if test(shapes[i])]
        if idxs:
            by_short.append(cs(lab, idxs))
    by_trump = []
    trump = strain if strain in _SUIT_IDX else None
    if trump:
        si = _SUIT_IDX[trump]
        for lab, test in ((f"4 {SUIT_SYM[trump]}", lambda L: L == 4),
                          (f"5 {SUIT_SYM[trump]}", lambda L: L == 5),
                          (f"6+ {SUIT_SYM[trump]}", lambda L: L >= 6)):
            idxs = [i for i in rng if test(shapes[i][si])]
            if idxs:
                by_trump.append(cs(lab, idxs))
    return Breakdown(seat, contract_label, trump, by_hcp, by_trump, by_short)


class Aborted(Exception):
    """Raised inside :func:`run` when the injected ``stop()`` returns True."""


def _score_lut(vul):
    return {cs: [Contract.from_str(cs, vul=vul).score(t) for t in range(14)]
            for cs in ALL_CS}


def run(config, solver=None, progress=lambda a, t: None, stop=lambda: False):
    """Run the simulation described by ``config`` and return a SimResult.

    :param solver: a Solver; defaults to the process-wide DdsSolver.
    :param progress: called ``(accepted, tries)`` after each solved batch.
    :param stop: polled between deals; return True to abort (raises Aborted).
    """
    solver = solver or default_solver()
    if config.seed not in (None, ""):
        random.seed(int(config.seed))

    dealer, accept = build_dealer(config)
    score_lut = _score_lut(config.vul)
    i0, i1 = SIDE_IDX[config.side]

    focus = smart_seat(config.specs)          # constrained seat to profile
    foc_attr = ATTR[focus] if focus else None
    fhcp, fshape, fst = [], [], []

    make = {lab: 0 for lab, *_ in GAMES + SLAMS}
    make.update({"any game": 0, "any slam": 0, "grand": 0})
    score = {lab: 0 for lab, *_ in GAMES + SLAMS}
    gvecs, svecs = [], []
    store = config.n <= _STORE_CAP
    samples, pending = [], []
    accepted = candidates = tries = 0

    def flush():
        nonlocal accepted
        if not pending:
            return
        for deal, tv in zip(pending, solver.solve(pending)):
            st = {s: max(v[i0], v[i1]) for s, v in tv.items()}
            g = s = False
            gvec = []
            for lab, strain, need, cs in GAMES:
                sc = score_lut[cs][st[strain]]
                score[lab] += sc; gvec.append(sc)
                if st[strain] >= need:
                    make[lab] += 1; g = True
            svec = []
            for lab, strain, need, cs in SLAMS:
                sc = score_lut[cs][st[strain]]
                score[lab] += sc; svec.append(sc)
                if st[strain] >= need:
                    make[lab] += 1; s = True
            make["any game"] += g
            make["any slam"] += s
            make["grand"] += max(st.values()) >= 13
            if store:
                gvecs.append(gvec); svecs.append(svec)
            if len(samples) < config.n_samples:
                samples.append(SampleDeal(
                    {seat: fmt_hand(getattr(deal, ATTR[seat])) for seat in ORDER},
                    dict(st)))
            if focus and store:
                fh = getattr(deal, foc_attr)
                fhcp.append(fh.hcp); fshape.append(tuple(fh.shape)); fst.append(st)
            accepted += 1
        pending.clear()

    while candidates < config.n:
        if stop():
            raise Aborted()
        if tries >= config.max_tries:
            break
        tries += 1
        deal = dealer()
        if not accept(deal):
            continue
        pending.append(deal)
        candidates += 1
        if len(pending) >= BATCH:
            flush()
            progress(accepted, tries)
    flush()

    if accepted == 0:
        return SimResult(config=config, accepted=0, tries=tries)

    def stat(lab, scored):
        return ContractStat(lab, make[lab], accepted,
                            score[lab] / accepted if scored else None)

    games = [stat(g[0], True) for g in GAMES]
    slams = [stat(s[0], True) for s in SLAMS]
    best_game = max(games, key=lambda c: c.avg_score)
    best_slam = max(slams, key=lambda c: c.avg_score)
    bg = games.index(best_game); bs = slams.index(best_slam)
    imp = None
    if store and gvecs:
        imp = sum(to_imps(svecs[k][bs] - gvecs[k][bg])
                  for k in range(len(gvecs))) / len(gvecs)

    # Profile the contract actually in question: the slam if it's a live option,
    # otherwise the game decision (game-vs-partscore).
    target = best_slam if best_slam.make_rate >= 12 else best_game
    breakdown = (_breakdown(focus, target.label, fhcp, fshape, fst)
                 if focus and fhcp else None)

    return SimResult(
        config=config, accepted=accepted, tries=tries,
        games=games, slams=slams,
        any_game=stat("any game", False), any_slam=stat("any slam", False),
        grand=stat("grand", False),
        best_game=best_game, best_slam=best_slam,
        ev_diff=best_slam.avg_score - best_game.avg_score, imp=imp,
        samples=samples, breakdown=breakdown)
