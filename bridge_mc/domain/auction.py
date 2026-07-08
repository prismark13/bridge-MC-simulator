"""Work out the declarer from an auction.

The double-dummy engine can solve a contract from any of the four seats, but a
real auction fixes *which* seat plays it: by law the declarer is the member of
the contract-winning side who **first named the final strain** (at any level).
On hands where a long suit sits opposite the tenaces, that single fact can swing
the make-rate by 20-30% — so the honest number is the one from the seat the
auction actually installs, not ``max(partner, me)``.
"""
from __future__ import annotations

SEATS = ("N", "E", "S", "W")
STRAINS = ("C", "D", "H", "S", "NT")
_SIDE = {"N": "NS", "S": "NS", "E": "EW", "W": "EW"}


def _norm(call: str) -> str:
    c = call.strip().upper().replace(" ", "")
    return {"PASS": "P", "DBL": "X", "DOUBLE": "X", "RDBL": "XX", "REDOUBLE": "XX"}.get(c, c)


def parse_bid(call: str):
    """('6', 'NT') for a real bid; None for pass/double/redouble."""
    c = _norm(call)
    if not c or c[0] not in "1234567":
        return None
    strain = c[1:]
    if strain == "N":            # accept bare 'N' as shorthand for notrump
        strain = "NT"
    if strain not in STRAINS:
        raise ValueError(f"bad strain in {call!r}")
    return int(c[0]), strain


def declarer_from_auction(dealer: str, calls):
    """Return dict(level, strain, side, declarer, doubled) or None if passed out.

    ``dealer`` is the seat that makes the first call; ``calls`` is the ordered
    list of calls that follow, going clockwise N->E->S->W.
    """
    if isinstance(calls, str):
        calls = calls.split()
    dealer = dealer.strip().upper()
    order = SEATS[SEATS.index(dealer):] + SEATS[:SEATS.index(dealer)]
    first_of: dict = {}          # (side, strain) -> first seat to name it
    last = None                  # (level, strain, seat)
    doubled = 0
    for i, call in enumerate(calls):
        seat = order[i % 4]
        n = _norm(call)
        if n in ("X", "XX"):
            doubled = 1 if n == "X" else 2
            continue
        if n == "P":
            continue
        bid = parse_bid(call)
        if bid is None:
            continue
        level, strain = bid
        doubled = 0                      # a fresh bid clears any pending double
        first_of.setdefault((_SIDE[seat], strain), seat)
        last = (level, strain, seat)
    if last is None:
        return None
    level, strain, seat = last
    side = _SIDE[seat]
    return {
        "level": level, "strain": strain, "side": side,
        "declarer": first_of[(side, strain)], "doubled": doubled,
    }
