"""Bridge constants and scoring-independent tables. Pure data, no dependencies."""

SUITS = ["S", "H", "D", "C"]
SUIT_SYM = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RANKS = "AKQJT98765432"
STRAINS = ["C", "D", "H", "S", "N"]
ORDER = ["N", "E", "S", "W"]
ATTR = {"N": "north", "E": "east", "S": "south", "W": "west"}
SIDE_IDX = {"NS": (0, 2), "EW": (1, 3)}

#            label  strain  need  contract-string
GAMES = [("3NT", "N", 9, "3N"), ("4H", "H", 10, "4H"), ("4S", "S", 10, "4S"),
         ("5C", "C", 11, "5C"), ("5D", "D", 11, "5D")]
SLAMS = [("6C", "C", 12, "6C"), ("6D", "D", 12, "6D"), ("6H", "H", 12, "6H"),
         ("6S", "S", 12, "6S"), ("6NT", "N", 12, "6N")]
ALL_CS = [g[3] for g in GAMES] + [s[3] for s in SLAMS]

# IMP table: upper bound of point-difference for each IMP value 0..24
_IMP_UP = [10, 40, 80, 120, 160, 210, 260, 310, 360, 420, 490, 590, 740, 890,
           1090, 1290, 1490, 1740, 1990, 2240, 2490, 2990, 3490, 3990]


def to_imps(diff):
    """Convert a raw point difference into IMPs (signed)."""
    a = abs(diff)
    n = 0
    for u in _IMP_UP:
        if a > u:
            n += 1
        else:
            break
    return n if diff >= 0 else -n
