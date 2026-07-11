"""The Monte-Carlo loop: sample deals, DD-solve in batches, aggregate.

Both sides are aggregated every run — 'us' (config.side) and 'them' — each
scored at its own vulnerability, so competitive-auction decisions (compete,
double, sacrifice) can be judged. UI-agnostic: cancellation and progress are
injected as callbacks.
"""
from __future__ import annotations

import random
from collections import Counter

from redeal import Contract

from ..domain.contracts import (
    ALL_CS, ATTR, GAMES, GRANDS, ORDER, SIDE_IDX, SLAMS, STRAINS, SUIT_SYM,
    opp_side, side_vul, to_imps)
from ..domain.auction import declarer_from_auction
from ..domain.types import (
    AuctionResult, Breakdown, ContractStat, Par, Sacrifice, SampleDeal,
    SimResult)
from .sampling import build_dealer, fmt_hand
from .scoring import sacrifice_deal
from .solver import BATCH, VUL_TO_DDS, default_solver

_STORE_CAP = 100_000     # cap for keeping per-deal score vectors (IMP swing)
_SUIT_IDX = {"S": 0, "H": 1, "D": 2, "C": 3}
_NEED = {lab: need for lab, _s, need, _cs in GAMES + SLAMS}
_DDS_STRAIN = {"C": "C", "D": "D", "H": "H", "S": "S", "NT": "N"}
_PARTNER = {"N": "S", "S": "N", "E": "W", "W": "E"}


def _auction_setup(config):
    """Parse ``config.auction`` into per-deal accumulation state, or None.

    Returns a dict tracking how often the contract makes as the auction installs
    the declarer (``dec_i``) vs from partner's seat (``par_i``) — the wrong-side
    swing. Bad/empty auctions and pass-outs yield None (no auction readout)."""
    if not config.auction or not config.auction.strip():
        return None
    try:
        info = declarer_from_auction(config.dealer, config.auction.split())
    except (ValueError, KeyError):
        return None
    if info is None:
        return None
    dec = info["declarer"]
    return {
        "contract": f"{info['level']}{info['strain']}",
        "declarer": dec, "partner": _PARTNER[dec], "side": info["side"],
        "doubled": info["doubled"], "dds": _DDS_STRAIN[info["strain"]],
        "need": info["level"] + 6,
        "dec_i": ORDER.index(dec), "par_i": ORDER.index(_PARTNER[dec]),
        "dec_makes": 0, "par_makes": 0,
    }


class Aborted(Exception):
    """Raised inside :func:`run` when the injected ``stop()`` returns True."""


def _focus_seat(specs, side):
    """The constrained seat on *our* side to profile, or None.

    We only ever profile our own hand ('which of our hands should bid on'),
    never an opponent's — so an EW analysis won't break down North's hand.
    """
    for seat in (("N", "S") if side == "NS" else ("E", "W")):
        sp = specs.get(seat)
        if sp and sp.kind == "con" and sp.constrains:
            return seat
    return None


def _score_lut(vul):
    return {cs: [Contract.from_str(cs, vul=vul).score(t) for t in range(14)]
            for cs in ALL_CS}


class _Acc:
    """Accumulates make-counts and scores for one side across the run.

    A contract is scored from the seat that would *realistically* declare it: for
    a suit, the longer-trump hand of the side (that hand always wins the auction
    for its suit); for notrump, the better of the two (either hand can play it).
    This avoids the optimistic ``max(partner, me)`` that credits a declarer the
    auction can never install.
    """
    __slots__ = ("a", "b", "seats", "lut", "store", "make", "score", "gvecs", "svecs")

    def __init__(self, idx, seats, lut, store):
        self.a, self.b = idx
        self.seats = seats
        self.lut = lut
        self.store = store
        self.make = {lab: 0 for lab, *_ in GAMES + SLAMS + GRANDS}
        self.make.update({"any game": 0, "any slam": 0, "grand": 0})
        self.score = {lab: 0 for lab, *_ in GAMES + SLAMS + GRANDS}
        self.gvecs, self.svecs = [], []

    def dec_idx(self, deal):
        """Per-strain DD index of this side's realistic declarer for one deal.

        None means 'use the better of the two hands' (notrump, or a tied suit
        length where either seat could end up declaring)."""
        ha = getattr(deal, ATTR[self.seats[0]]).shape
        hb = getattr(deal, ATTR[self.seats[1]]).shape
        d = {"N": None}
        for s in ("S", "H", "D", "C"):
            la, lb = ha[_SUIT_IDX[s]], hb[_SUIT_IDX[s]]
            d[s] = self.a if la > lb else self.b if lb > la else None
        return d

    def add(self, tv, di):
        """Fold one deal's DD table in; return this side's per-strain tricks."""
        st = {s: (max(v[self.a], v[self.b]) if di[s] is None else v[di[s]])
              for s, v in tv.items()}
        g = sl = False
        gvec = []
        for lab, strain, need, cs in GAMES:
            sc = self.lut[cs][st[strain]]
            self.score[lab] += sc; gvec.append(sc)
            if st[strain] >= need:
                self.make[lab] += 1; g = True
        svec = []
        for lab, strain, need, cs in SLAMS:
            sc = self.lut[cs][st[strain]]
            self.score[lab] += sc; svec.append(sc)
            if st[strain] >= need:
                self.make[lab] += 1; sl = True
        for lab, strain, need, cs in GRANDS:
            self.score[lab] += self.lut[cs][st[strain]]
            if st[strain] >= need:
                self.make[lab] += 1
        self.make["any game"] += g
        self.make["any slam"] += sl
        self.make["grand"] += max(st.values()) >= 13
        if self.store:
            self.gvecs.append(gvec); self.svecs.append(svec)
        return st


def _stats(acc, accepted, pos=None):
    """Turn an accumulator into ContractStats + best game/slam + EV/IMP."""
    def stat(lab, scored):
        pr, se = pos.get(lab, (0, 0)) if pos else (0, 0)
        return ContractStat(lab, acc.make[lab], accepted,
                            acc.score[lab] / accepted if scored else None,
                            proof=pr, sens=se)

    games = [stat(g[0], True) for g in GAMES]
    slams = [stat(s[0], True) for s in SLAMS]
    grands = [stat(g[0], True) for g in GRANDS]
    best_game = max(games, key=lambda c: c.avg_score)
    best_slam = max(slams, key=lambda c: c.avg_score)
    best_grand = max(grands, key=lambda c: c.avg_score)
    imp = None
    if acc.store and acc.gvecs:
        bg, bs = games.index(best_game), slams.index(best_slam)
        imp = sum(to_imps(acc.svecs[k][bs] - acc.gvecs[k][bg])
                  for k in range(len(acc.gvecs))) / len(acc.gvecs)
    return {"games": games, "slams": slams, "grands": grands,
            "any_game": stat("any game", False), "any_slam": stat("any slam", False),
            "grand": stat("grand", False), "best_game": best_game,
            "best_slam": best_slam, "best_grand": best_grand,
            "ev_diff": best_slam.avg_score - best_game.avg_score, "imp": imp}


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


def run(config, solver=None, progress=lambda a, t: None, stop=lambda: False):
    """Run the simulation described by ``config`` and return a SimResult.

    Aggregates both sides; the protagonist ('us') side is ``config.side`` and
    gets the bidding-decision readout + breakdown, while the other side ('them')
    is reported for competitive judgement.
    """
    solver = solver or default_solver()
    if config.seed not in (None, ""):
        random.seed(int(config.seed))

    dealer, accept = build_dealer(config)
    store = config.n <= _STORE_CAP
    us_side, them_side = config.side, opp_side(config.side)
    vul_us = side_vul(config.vul, us_side)
    vul_them = side_vul(config.vul, them_side)
    # The finesse swap exchanges the two DEFENDERS' hands; it's only valid when
    # both are unconstrained (else it moves a shown suit to the wrong opponent).
    opp_seats = ("E", "W") if us_side == "NS" else ("N", "S")
    finesse_ok = config.finesse and all(
        config.specs[s].kind == "random" for s in opp_seats)
    us = _Acc(SIDE_IDX[us_side], (us_side[0], us_side[1]), _score_lut(vul_us), store)
    them = _Acc(SIDE_IDX[them_side], (them_side[0], them_side[1]),
                _score_lut(vul_them), store)
    sac_pass = sac_bid = 0.0
    sac_better = 0
    sac_lab, sac_opp = Counter(), Counter()

    focus = _focus_seat(config.specs, config.side)   # our constrained seat, if any
    foc_attr = ATTR[focus] if focus else None
    fhcp, fshape, fst = [], [], []

    par_vul = VUL_TO_DDS.get(config.vul, 0)
    par_sum = 0.0
    par_sac = 0
    par_ctr = Counter()

    _all = GAMES + SLAMS + GRANDS
    pos_both = {lab: 0 for lab, *_ in _all}     # makes regardless of the E/W split
    pos_one = {lab: 0 for lab, *_ in _all}      # makes in exactly one orientation

    # Optional explicit auction -> the seat that actually declares the final
    # contract, so we can report the reachable make-rate and the wrong-side cost.
    auc = _auction_setup(config)

    # Full trick distribution: per strain, per seat, a histogram of DD tricks.
    # Lets any contract's make-rate (incl. partscores like 3D by West) be
    # resolved after the run — the data behind the reactive "Ask Claude".
    tdist = {s: {"N": Counter(), "E": Counter(), "S": Counter(), "W": Counter()}
             for s in STRAINS}

    samples, pending = [], []
    accepted = candidates = tries = 0

    def flush():
        nonlocal accepted, par_sum, par_sac, sac_pass, sac_bid, sac_better
        if not pending:
            return
        full = solver.solve_full(pending, par_vul)
        # For the finesse split, DD-solve each deal with the two defenders swapped.
        sw = None
        if finesse_ok:
            oa, ob = SIDE_IDX[them_side]
            batch = []
            for d in pending:
                hs = [d.north, d.east, d.south, d.west]
                hs[oa], hs[ob] = hs[ob], hs[oa]
                batch.append(hs)
            sw = solver.solve(batch)
        for idx, (deal, (tv, par)) in enumerate(zip(pending, full)):
            di_us = us.dec_idx(deal)
            st_us = us.add(tv, di_us)
            them_st = them.add(tv, them.dec_idx(deal))
            for s in STRAINS:
                col = tv[s]
                row = tdist[s]
                row["N"][col[0]] += 1; row["E"][col[1]] += 1
                row["S"][col[2]] += 1; row["W"][col[3]] += 1
            if auc is not None:
                col = tv[auc["dds"]]
                auc["dec_makes"] += col[auc["dec_i"]] >= auc["need"]
                auc["par_makes"] += col[auc["par_i"]] >= auc["need"]
            if sw is not None:
                swt = sw[idx]
                for lab, strain, need, cs in _all:
                    nm = st_us[strain] >= need
                    di = di_us[strain]
                    swept = max(swt[strain][us.a], swt[strain][us.b]) if di is None \
                        else swt[strain][di]
                    sm = swept >= need
                    if nm and sm:
                        pos_both[lab] += 1
                    elif nm or sm:
                        pos_one[lab] += 1
            pe, be, lab, olab = sacrifice_deal(st_us, them_st, vul_us, vul_them)
            sac_pass += pe; sac_bid += be; sac_better += be > pe
            sac_lab[lab] += 1; sac_opp[olab] += 1
            if par is not None:
                par_sum += par["ns"] if us_side == "NS" else par["ew"]
                par_sac += par["sac"]
                par_ctr[par["contract"]] += 1
            if len(samples) < config.n_samples:
                ps = 0
                pc = ""
                if par is not None:
                    ps = par["ns"] if us_side == "NS" else par["ew"]
                    pc = par["contract"]
                samples.append(SampleDeal(
                    {seat: fmt_hand(getattr(deal, ATTR[seat])) for seat in ORDER},
                    dict(st_us), par=pc, par_score=ps))
            if focus and store:
                fh = getattr(deal, foc_attr)
                fhcp.append(fh.hcp); fshape.append(tuple(fh.shape)); fst.append(st_us)
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

    pos = ({lab: (pos_both[lab], pos_one[lab]) for lab in pos_both}
           if finesse_ok else None)
    U = _stats(us, accepted, pos)
    T = _stats(them, accepted)

    finesse_note = ""
    if config.finesse and not finesse_ok:
        con = [s for s in opp_seats if config.specs[s].kind != "random"]
        finesse_note = (
            "Card-placement (finesse) split skipped: it needs both defenders "
            f"unconstrained, but {'/'.join(con)} "
            f"{'is' if len(con) == 1 else 'are'} constrained — swapping would move a "
            "shown suit to the wrong opponent.")

    par = Par(avg_us=par_sum / accepted, sac_rate=par_sac / accepted,
              top=par_ctr.most_common(3))

    # Which analysis fits this deal set: slam / game / competitive (partscore).
    if U["best_slam"].make_rate >= 45:
        zone = "slam"
    elif U["best_game"].make_rate >= 55:
        zone = "game"
    else:
        zone = "competitive"

    # Breakdown only on constructive (slam/game) deals, profiling OUR hand: the
    # slam when it's a live invitation (>=12%), otherwise the game.
    if zone in ("slam", "game") and focus and fhcp:
        target = U["best_slam"] if U["best_slam"].make_rate >= 12 else U["best_game"]
        breakdown = _breakdown(focus, target.label, fhcp, fshape, fst)
    else:
        breakdown = None

    sacrifice = Sacrifice(
        opp_game=sac_opp.most_common(1)[0][0] if sac_opp else "",
        save_bid=sac_lab.most_common(1)[0][0] if sac_lab else "",
        avg_pass=sac_pass / accepted, avg_bid=sac_bid / accepted,
        bid_better=sac_better / accepted)

    auction = None
    if auc is not None:
        auction = AuctionResult(
            contract=auc["contract"], declarer=auc["declarer"],
            partner=auc["partner"], side=auc["side"],
            on_our_side=(auc["side"] == us_side), doubled=auc["doubled"],
            dec_makes=auc["dec_makes"], par_makes=auc["par_makes"],
            trials=accepted)

    return SimResult(
        config=config, accepted=accepted, tries=tries,
        games=U["games"], slams=U["slams"], grands=U["grands"],
        any_game=U["any_game"], any_slam=U["any_slam"], grand=U["grand"],
        best_game=U["best_game"], best_slam=U["best_slam"],
        best_grand=U["best_grand"], ev_diff=U["ev_diff"], imp=U["imp"],
        samples=samples, breakdown=breakdown,
        opp_games=T["games"], opp_slams=T["slams"],
        opp_best_game=T["best_game"], opp_best_slam=T["best_slam"],
        par=par, zone=zone, sacrifice=sacrifice, finesse=finesse_ok,
        finesse_note=finesse_note, auction=auction,
        trick_dist={s: {k: dict(v) for k, v in seats.items()}
                    for s, seats in tdist.items()})
