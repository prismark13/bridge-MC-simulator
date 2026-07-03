import random
from endplay.types import Deal, Denom, Player
from endplay.dds import calc_dd_table

random.seed(20260702)

RANKS = "AKQJT98765432"
HCP = {"A":4,"K":3,"Q":2,"J":1}
SUITS = ["S","H","D","C"]  # spades, hearts, diamonds, clubs

# South (us): SQ9643 HJ DAT86 CKQ4
south = {
    "S": set("Q9643"),
    "H": set("J"),
    "D": set("AT86"),
    "C": set("KQ4"),
}

def hcp(cards_by_suit):
    return sum(HCP.get(r,0) for s in SUITS for r in cards_by_suit[s])

print("South HCP:", hcp(south),
      "shape S/H/D/C:", [len(south[s]) for s in SUITS])

# Build 39-card pool (deck minus South)
pool = [(s, r) for s in SUITS for r in RANKS if r not in south[s]]
assert len(pool) == 39

def suit_str(cards):
    # order by rank high->low for PBN
    return "".join(r for r in RANKS if r in cards)

def to_pbn(north, east, west):
    def h(d): return ".".join(suit_str(d[s]) for s in SUITS)
    # PBN order after "N:" is N E S W
    return f"N:{h(north)} {h(east)} {h(south)} {h(west)}"

def make_hand(cards):
    d = {s:set() for s in SUITS}
    for (s,r) in cards:
        d[s].add(r)
    return d

DENOM = {"S":Denom.spades,"H":Denom.hearts,"D":Denom.diamonds,"C":Denom.clubs,"NT":Denom.nt}

def ns_tricks(table, denom):
    # best of North / South as declarer
    return max(table[DENOM[denom], Player.north], table[DENOM[denom], Player.south])

N_TARGET = 3000
accepted = 0
tries = 0
records = []

while accepted < N_TARGET:
    tries += 1
    hand = random.sample(pool, 13)
    north = make_hand(hand)
    nh = len(north["H"]); nd = len(north["D"])
    if nh < 5 or nd < 4:
        continue
    h = hcp(north)
    if h < 16 or h > 21:
        continue
    # constraints met; deal opponents
    rest = [c for c in pool if c not in set(hand)]
    random.shuffle(rest)
    east = make_hand(rest[:13])
    west = make_hand(rest[13:])
    deal = Deal(to_pbn(north, east, west))
    table = calc_dd_table(deal)
    dK = "K" in north["D"]; dQ = "Q" in north["D"]; dJ = "J" in north["D"]
    d_hontop = sum([dK,dQ,dJ])  # partner top-diamond honors (we hold A,T)
    aces = sum(1 for s in SUITS if "A" in north[s])
    rec = {
        "hcp": h,
        "shape": (len(north["S"]),nh,nd,len(north["C"])),
        "dK": dK, "d_hontop": d_hontop, "aces": aces,
        "D": ns_tricks(table,"D"),
        "H": ns_tricks(table,"H"),
        "S": ns_tricks(table,"S"),
        "C": ns_tricks(table,"C"),
        "NT": ns_tricks(table,"NT"),
        "north": to_pbn(north,east,west).split()[0][2:],  # north holding
    }
    records.append(rec)
    accepted += 1

print(f"tries={tries} accepted={accepted} (accept rate {accepted/tries:.1%})")

def pct(cond):
    return 100.0*sum(1 for r in records if cond(r))/len(records)

six_d   = lambda r: r["D"]  >= 12
five_d  = lambda r: r["D"]  >= 11
three_nt= lambda r: r["NT"] >= 9
six_h   = lambda r: r["H"]  >= 12
four_h  = lambda r: r["H"]  >= 10
four_s  = lambda r: r["S"]  >= 10
any_slam= lambda r: r["D"]>=12 or r["H"]>=12 or r["S"]>=12 or r["NT"]>=12 or r["C"]>=12

print()
print("=== Contract frequencies (double-dummy, best NS declarer) ===")
print(f"6D makes (>=12 D tricks):   {pct(six_d):5.1f}%")
print(f"5D makes (>=11 D tricks):   {pct(five_d):5.1f}%")
print(f"3NT makes (>=9 NT tricks):  {pct(three_nt):5.1f}%")
print(f"4H makes (>=10 H tricks):   {pct(four_h):5.1f}%")
print(f"6H makes (>=12 H tricks):   {pct(six_h):5.1f}%")
print(f"any slam makes:             {pct(any_slam):5.1f}%")

# "3NT is the last good spot": 3NT makes, but NO 11-trick minor game and NO slam,
# i.e. best you can safely reach is game and 3NT is it (5D fails, no slam).
def nt_is_ceiling(r):
    if r["NT"] < 9: return False          # 3NT must make
    if r["D"] >= 11: return False          # would prefer 5D/6D
    if r["H"] >= 11: return False          # 5H/6H available
    if any_slam(r): return False
    return True

print()
print("=== Classification ===")
print(f"6D good (makes):                        {pct(six_d):5.1f}%")
print(f"3NT is the ceiling (best makeable game):{pct(nt_is_ceiling):5.1f}%")

# average trick counts
import statistics as st
print()
print("Avg DD tricks:  D=%.1f  H=%.1f  NT=%.1f  S=%.1f" % (
    st.mean(r["D"] for r in records),
    st.mean(r["H"] for r in records),
    st.mean(r["NT"] for r in records),
    st.mean(r["S"] for r in records)))

# Profile of 6D-making hands vs 3NT-ceiling hands
def profile(name, cond):
    sub = [r for r in records if cond(r)]
    if not sub:
        print(f"\n{name}: (none)"); return
    print(f"\n{name}: n={len(sub)} ({100*len(sub)/len(records):.0f}%)")
    print(f"  avg HCP: {st.mean(r['hcp'] for r in sub):.1f}")
    print(f"  avg diamond length: {st.mean(r['shape'][2] for r in sub):.1f}")
    print(f"  avg heart length:   {st.mean(r['shape'][1] for r in sub):.1f}")
    # example hands
    for r in sub[:4]:
        print(f"    e.g. HCP{r['hcp']} shape{r['shape']}  D={r['D']} NT={r['NT']} H={r['H']}  N: {r['north']}")

profile("6D-making hands", six_d)
profile("3NT-ceiling hands", nt_is_ceiling)

def dstats(name, cond):
    sub = [r for r in records if cond(r)]
    if not sub: return
    print(f"\n{name}: partner holds DK {100*st.mean(r['dK'] for r in sub):.0f}%,"
          f" avg top-D honors(K/Q/J) {st.mean(r['d_hontop'] for r in sub):.2f},"
          f" avg aces {st.mean(r['aces'] for r in sub):.2f}")
print("\n=== Diamond quality / controls ===")
dstats("6D-making", six_d)
dstats("6D-failing", lambda r: not six_d(r))
dstats("3NT-ceiling", nt_is_ceiling)

# 6D make-rate conditioned on partner holding the DK
print()
for label, cond in [("partner has DK", lambda r: r["dK"]),
                    ("partner lacks DK", lambda r: not r["dK"])]:
    sub=[r for r in records if cond(r)]
    print(f"  {label}: 6D makes {100*sum(1 for r in sub if six_d(r))/len(sub):.0f}%  (n={len(sub)})")
# 6D make-rate by combined HCP
print()
for lo,hi in [(16,17),(18,19),(20,21)]:
    sub=[r for r in records if lo<=r["hcp"]<=hi]
    if sub:
        print(f"  partner {lo}-{hi} HCP (combined {12+lo}-{12+hi}): 6D makes {100*sum(1 for r in sub if six_d(r))/len(sub):.0f}%  (n={len(sub)})")
