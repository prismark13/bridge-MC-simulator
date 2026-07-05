"""Deal generation.

One constrained seat is importance-sampled with redeal's SmartStack so its
shape *and* HCP are satisfied on every deal (no rejection) — this covers
balanced/semi-balanced hands **and** minimum-length hands like '5+ hearts,
4+ diamonds'. Any *other* constrained seats are enforced by rejection.
"""
from redeal import Deal, H, Shape, SmartStack, balanced, hcp, semibalanced

from ..domain.contracts import ATTR, SUITS, SUIT_SYM

SHAPE_TEST = {"bal": balanced, "semibal": semibalanced}


def fmt_hand(hand) -> str:
    """redeal Hand -> '♠AK5 ♥QJT ♦9432 ♣K8'."""
    return " ".join(f"{SUIT_SYM[s]}{x or '-'}" for s, x in
                    zip(SUITS, (hand.spades, hand.hearts,
                                hand.diamonds, hand.clubs)))


def _shape_for(sp) -> Shape:
    """redeal Shape capturing a spec's shape/length (HCP handled separately)."""
    if sp.shape == "bal":
        return balanced
    if sp.shape == "semibal":
        return semibalanced
    if sp.shape == "minlen" and any(sp.mins):
        mn = sp.mins
        return Shape.from_cond(
            lambda s, h, d, c: s >= mn[0] and h >= mn[1] and d >= mn[2] and c >= mn[3])
    # 'any' shape (HCP-only constraint): full wildcard, SmartStack biases HCP.
    return Shape.from_cond(lambda s, h, d, c: True)


def smart_seat(specs):
    """The constrained seat to importance-sample: the first that actually filters."""
    for seat, sp in specs.items():
        if sp.kind == "con" and sp.constrains:
            return seat
    return None


def build_dealer(config):
    """-> (dealer, accept). The smart seat is guaranteed by SmartStack; ``accept``
    applies rejection filters for any remaining constrained seats."""
    specs = config.specs
    predeal = {seat: H(sp.fixed) for seat, sp in specs.items()
               if sp.kind == "fixed"}
    smart = smart_seat(specs)
    if smart:
        sp = specs[smart]
        predeal[smart] = SmartStack(_shape_for(sp), hcp, range(sp.lo, sp.hi + 1))
    rej = [(seat, sp) for seat, sp in specs.items()
           if sp.kind == "con" and seat != smart and sp.constrains]

    def accept(deal):
        for seat, sp in rej:
            hand = getattr(deal, ATTR[seat])
            if not (sp.lo <= hand.hcp <= sp.hi):
                return False
            if sp.shape in SHAPE_TEST and not SHAPE_TEST[sp.shape](hand):
                return False
            if sp.shape == "minlen":
                sh = hand.shape
                if any(sh[i] < sp.mins[i] for i in range(4)):
                    return False
        return True

    return Deal.prepare(predeal), accept
