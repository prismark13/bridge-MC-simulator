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


def _len_token(tok):
    """One suit-length spec -> (min, max). 'x'/'' any, 'n' min-n, 'n+' min-n, 'a-b' range."""
    t = tok.strip().lower()
    if t in ("", "x"):
        return 0, 13
    if t.endswith("+"):
        return int(t[:-1]), 13
    if "-" in t:
        a, b = t.split("-", 1)
        return int(a), int(b)
    return int(t), 13                    # plain digit = minimum (backward compatible)


def parse_shape(text):
    """-> (kind, mins, maxs) where kind in {'any','bal','semibal','minlen'}.

    Length specs accept a min ('5', '5+'), a range ('3-5'), or any ('x'):
    e.g. '0 5 4 0' (5+H 4+D) or '3-5 5+ 0-4 x'.
    """
    t = text.strip().lower()
    if t in ("", "any"):
        return "any", [0, 0, 0, 0], [13, 13, 13, 13]
    if t in ("bal", "balanced"):
        return "bal", [0, 0, 0, 0], [13, 13, 13, 13]
    if t in ("semi", "semibal", "semibalanced"):
        return "semibal", [0, 0, 0, 0], [13, 13, 13, 13]
    parts = t.split()
    if len(parts) == 4:
        mins, maxs = [], []
        for p in parts:
            try:
                a, b = _len_token(p)
            except ValueError:
                raise ValueError(f"bad suit length {p!r}")
            if not (0 <= a <= b <= 13):
                raise ValueError(f"suit length {p!r} out of range 0-13")
            mins.append(a); maxs.append(b)
        if sum(mins) > 13:
            raise ValueError(f"min lengths sum to {sum(mins)} (>13)")
        if sum(maxs) < 13:
            raise ValueError(f"max lengths sum to {sum(maxs)} (<13)")
        return "minlen", mins, maxs
    raise ValueError("use 'bal', 'semibal', 'any', or 4 suit lengths "
                     "like '0 5 4 0' or '3-5 5+ 0-4 x'")


def _num_range(s, dflo, dfhi):
    s = s.strip()
    if not s:
        return dflo, dfhi
    if s.endswith("+"):
        return int(s[:-1]), dfhi
    if "-" in s:
        a, b = s.split("-", 1)
        return int(a), int(b)
    return int(s), int(s)


def parse_honors(text):
    """-> (holdings, tops, (ctrl_lo, ctrl_hi)).

    Tokens (space/comma separated), each scoped to a suit S/H/D/C:
      'DAK'    holding: has the named cards (♦A, ♦K)
      'HQxx'   holding: has ♥Q plus at least two small cards (x = a 2-9 spot)
      'Sxx'    holding: at least two small spades
      'H2/3'   at least 2 of the top 3 in hearts (any 2 of A/K/Q)
      'ctrl3-5' / 'ctrl3+' / 'ctrl3'   controls (A=2, K=1) range

    A holding is stored as (suit, named_ranks, x_count).
    """
    t = (text or "").strip()
    if not t:
        return (), (), (0, 12)
    holdings, tops = [], []
    clo, chi = 0, 12
    for raw in t.replace(",", " ").split():
        tok = raw.strip().upper()
        if not tok:
            continue
        if tok.startswith("CTRL"):
            clo, chi = _num_range(tok[4:].lstrip(":"), 0, 12)
            if not (0 <= clo <= chi <= 12):
                raise ValueError(f"bad controls {raw!r}")
        elif "/" in tok and tok[:1] in "SHDC":
            suit = tok[0]
            n, _, m = tok[1:].partition("/")
            try:
                n, m = int(n), int(m)
            except ValueError:
                raise ValueError(f"bad honor spec {raw!r}")
            if not (1 <= n <= m <= 13):
                raise ValueError(f"bad honor spec {raw!r}")
            tops.append((suit, n, m))
        elif len(tok) >= 2 and tok[0] in "SHDC":
            suit = tok[0]
            named, xc = [], 0
            for ch in tok[1:]:
                if ch == "X":
                    xc += 1
                elif ch in RANKS:
                    named.append(ch)
                else:
                    raise ValueError(f"bad card in {raw!r}")
            holdings.append((suit, tuple(named), xc))
        else:
            raise ValueError(f"bad honor token {raw!r}")
    return tuple(holdings), tuple(tops), (clo, chi)


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
            try:
                kind, mins, maxs = parse_shape(r["shape"])
            except ValueError as e:
                raise ValueError(f"{seat} shape: {e}")
            try:
                holdings, tops, ctrl = parse_honors(r.get("honors", ""))
            except ValueError as e:
                raise ValueError(f"{seat} honors: {e}")
            specs[seat] = SeatSpec.constrained(lo, hi, kind, mins, maxs,
                                               holdings, tops, ctrl)
        else:
            specs[seat] = SeatSpec.random()
    return specs
