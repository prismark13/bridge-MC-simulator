"""Deal generation.

One constrained seat is importance-sampled with redeal's SmartStack so its
shape *and* HCP are satisfied on every deal (no rejection) — this covers
balanced/semi-balanced hands **and** minimum-length hands like '5+ hearts,
4+ diamonds'. Any *other* constrained seats are enforced by rejection.
"""
from redeal import Deal, H, Shape, SmartStack, balanced, hcp, semibalanced

from ..domain.contracts import ATTR, RANKS, SUITS, SUIT_SYM

SHAPE_TEST = {"bal": balanced, "semibal": semibalanced}
_SUIT_ATTR = {"S": "spades", "H": "hearts", "D": "diamonds", "C": "clubs"}
_SMALL = "98765432"


def honors_ok(sp, hand) -> bool:
    """Does ``hand`` satisfy the seat's holding / top-of-suit / controls honours?"""
    if not sp.has_honors:
        return True
    h = {s: str(getattr(hand, _SUIT_ATTR[s])) for s in "SHDC"}
    for suit, named, xc in sp.holdings:
        hh = h[suit]
        for r in named:
            if r not in hh:
                return False
        if xc:
            smalls = sum(1 for r in hh if r in _SMALL and r not in named)
            if smalls < xc:
                return False
    for suit, n, m in sp.tops:
        if sum(1 for r in RANKS[:m] if r in h[suit]) < n:
            return False
    if sp.ctrl_lo > 0 or sp.ctrl_hi < 12:
        aces = sum(1 for s in "SHDC" if "A" in h[s])
        kings = sum(1 for s in "SHDC" if "K" in h[s])
        if not (sp.ctrl_lo <= 2 * aces + kings <= sp.ctrl_hi):
            return False
    return True


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
    if sp.shape == "minlen" and (any(sp.mins) or any(x < 13 for x in sp.maxs)):
        mn, mx = sp.mins, sp.maxs
        return Shape.from_cond(
            lambda s, h, d, c: mn[0] <= s <= mx[0] and mn[1] <= h <= mx[1]
            and mn[2] <= d <= mx[2] and mn[3] <= c <= mx[3])
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
    # Honours aren't handled by SmartStack, so check them on every constrained
    # seat (including the smart one) by rejection.
    hon = [(seat, sp) for seat, sp in specs.items()
           if sp.kind == "con" and sp.has_honors]

    def accept(deal):
        for seat, sp in rej:
            hand = getattr(deal, ATTR[seat])
            if not (sp.lo <= hand.hcp <= sp.hi):
                return False
            if sp.shape in SHAPE_TEST and not SHAPE_TEST[sp.shape](hand):
                return False
            if sp.shape == "minlen":
                sh = hand.shape
                if any(sh[i] < sp.mins[i] or sh[i] > sp.maxs[i] for i in range(4)):
                    return False
        for seat, sp in hon:
            if not honors_ok(sp, getattr(deal, ATTR[seat])):
                return False
        return True

    return Deal.prepare(predeal), accept
