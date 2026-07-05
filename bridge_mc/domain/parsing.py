"""Input parsing and validation. Pure: raises ValueError, knows nothing of Qt.

`build_specs` turns raw per-seat form fields into a validated ``dict[str,
SeatSpec]`` and is the single place that owns cross-seat rules (e.g. the same
card cannot be fixed in two hands).
"""
from .contracts import ORDER, RANKS, SUITS, SUIT_SYM
from .types import SeatSpec


def parse_suit(tok):
    t = tok.strip().upper().replace("10", "T")
    if t in ("", "-", "VOID"):
        return ""
    bad = [c for c in t if c not in RANKS]
    if bad:
        raise ValueError(f"invalid card(s) {''.join(bad)!r}")
    if len(set(t)) != len(t):
        raise ValueError("duplicate card in a suit")
    return t


def parse_fixed(text):
    """'AK5 QJT 9432 K8' -> (normalized_string, {(suit, rank), ...})."""
    toks = text.split()
    if len(toks) != 4:
        raise ValueError("need 4 suits separated by spaces, e.g. 'AK5 QJT 9432 K8'")
    holds, total = {}, 0
    for s, tok in zip(SUITS, toks):
        try:
            holds[s] = parse_suit(tok)
        except ValueError as e:
            raise ValueError(f"{SUIT_SYM[s]}: {e}")
        total += len(holds[s])
    if total != 13:
        raise ValueError(f"{total} cards - a fixed hand needs exactly 13")
    cards = {(s, r) for s in SUITS for r in holds[s]}
    return " ".join(holds[s] or "-" for s in SUITS), cards


def parse_shape(text):
    """-> (kind, mins) where kind in {'any','bal','semibal','minlen'}."""
    t = text.strip().lower()
    if t in ("", "any"):
        return "any", [0, 0, 0, 0]
    if t in ("bal", "balanced"):
        return "bal", [0, 0, 0, 0]
    if t in ("semi", "semibal", "semibalanced"):
        return "semibal", [0, 0, 0, 0]
    parts = t.split()
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        mins = [int(p) for p in parts]
        if sum(mins) > 13:
            raise ValueError(f"min lengths sum to {sum(mins)} (>13)")
        return "minlen", mins
    raise ValueError("use 'bal', 'semibal', 'any', or 4 min-lengths like '0 5 4 0'")


def build_specs(raw):
    """Validate raw per-seat inputs into ``dict[str, SeatSpec]``.

    ``raw[seat]`` is a mapping with keys: mode ('Random'|'Fixed'|'Constrain'),
    hand (str), lo (int), hi (int), shape (str). Raises ValueError on any
    problem, with the offending seat named.
    """
    specs, fixed_cards = {}, {}
    for seat in ORDER:
        r = raw[seat]
        mode = r["mode"]
        if mode == "Fixed":
            hstr, cards = parse_fixed(r["hand"])
            for cd in cards:
                if cd in fixed_cards:
                    raise ValueError(f"{seat}: {SUIT_SYM[cd[0]]}{cd[1]} "
                                     f"also in {fixed_cards[cd]}")
                fixed_cards[cd] = seat
            specs[seat] = SeatSpec.of_fixed(hstr)
        elif mode == "Constrain":
            lo, hi = int(r["lo"]), int(r["hi"])
            if lo > hi:
                raise ValueError(f"{seat}: HCP min > max")
            kind, mins = parse_shape(r["shape"])
            specs[seat] = SeatSpec.constrained(lo, hi, kind, mins)
        else:
            specs[seat] = SeatSpec.random()
    return specs
