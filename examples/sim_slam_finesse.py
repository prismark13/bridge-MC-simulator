import random, statistics as st
from endplay.types import Deal, Denom, Player
from endplay.dds import calc_dd_table

random.seed(20260703)

RANKS = "AKQJT98765432"
HCP = {"A":4,"K":3,"Q":2,"J":1}
SUITS = ["S","H","D","C"]

# South (us): S9 HJ6 DK9753 CQJ654
south = {"S":set("9"), "H":set("J6"), "D":set("K9753"), "C":set("QJ654")}

def hcp(d): return sum(HCP.get(r,0) for s in SUITS for r in d[s])
print("South HCP:", hcp(south), "shape:", [len(south[s]) for s in SUITS])

pool = [(s,r) for s in SUITS for r in RANKS if r not in south[s]]
assert len(pool)==39

def suit_str(cards): return "".join(r for r in RANKS if r in cards)
def hstr(d): return ".".join(suit_str(d[s]) for s in SUITS)
def pbn(n,e,s,w): return f"N:{hstr(n)} {hstr(e)} {hstr(s)} {hstr(w)}"
def make_hand(cards):
    d={s:set() for s in SUITS}
    for (s,r) in cards: d[s].add(r)
    return d

DENOM={"S":Denom.spades,"H":Denom.hearts,"D":Denom.diamonds,"C":Denom.clubs,"NT":Denom.nt}
BAL = {(3,3,3,4),(2,3,4,4),(2,3,3,5)}  # balanced patterns (sorted lengths)

def best(table, den):  # best of N/S declarer -> (tricks, seat)
    tn=table[DENOM[den],Player.north]; ts=table[DENOM[den],Player.south]
    return (tn,Player.north) if tn>=ts else (ts,Player.south)

N_TARGET=2500
acc=0; tries=0; R=[]
while acc<N_TARGET:
    tries+=1
    hand=random.sample(pool,13)
    north=make_hand(hand)
    shp=tuple(sorted(len(north[s]) for s in SUITS))
    if shp not in BAL: continue
    h=hcp(north)
    if h<22 or h>24: continue
    rest=[c for c in pool if c not in set(hand)]
    random.shuffle(rest)
    east=make_hand(rest[:13]); west=make_hand(rest[13:])
    t=calc_dd_table(Deal(pbn(north,east,south,west)))
    # pick better minor slam
    dC,seatC=best(t,"C"); dD,seatD=best(t,"D")
    if dD>=dC: mden,mtr,mseat="D",dD,seatD
    else:      mden,mtr,mseat="C",dC,seatC
    nt6,_=best(t,"NT"); nt3=nt6
    # finesse test: swap E/W, keep same denom+declarer, re-solve
    t2=calc_dd_table(Deal(pbn(north,west,south,east)))
    mtr_sw=t2[DENOM[mden],mseat]
    aces=sum(1 for s in SUITS if "A" in north[s])
    kings=sum(1 for s in SUITS if "K" in north[s])
    spade_hcp=sum(HCP.get(r,0) for r in north["S"])      # wasted opposite our stiff spade
    # fitting honors in the chosen minor (we hold DK / CQJ)
    minor_top=sum(1 for r in "AKQJ" if r in north[mden])
    R.append({
        "hcp":h, "shape":[len(north[s]) for s in SUITS],
        "C":dC,"D":dD,"NT":nt6,"minor":mden,"mtr":mtr,"mtr_sw":mtr_sw,
        "north":hstr(north),"fit":len(north[mden])+len(south[mden]),
        "aces":aces,"kings":kings,"spade_hcp":spade_hcp,"minor_top":minor_top,
    })
    acc+=1

print(f"tries={tries} accepted={acc} (rate {acc/tries:.2%})")
n=len(R)
def pc(c): return 100*sum(1 for r in R if c(r))/n

print("\n=== Make rates (double-dummy, best NS declarer) ===")
print(f"3NT (>=9 NT):            {pc(lambda r:r['NT']>=9):5.1f}%")
print(f"5-minor (>=11):          {pc(lambda r:max(r['C'],r['D'])>=11):5.1f}%")
print(f"6C (>=12):               {pc(lambda r:r['C']>=12):5.1f}%")
print(f"6D (>=12):               {pc(lambda r:r['D']>=12):5.1f}%")
print(f"6 best-minor (>=12):     {pc(lambda r:r['mtr']>=12):5.1f}%")
print(f"6NT (>=12):              {pc(lambda r:r['NT']>=12):5.1f}%")
print(f"any 12-trick slam:       {pc(lambda r:max(r['C'],r['D'],r['NT'])>=12):5.1f}%")
print(f"7 best-minor (>=13):     {pc(lambda r:r['mtr']>=13):5.1f}%")

# Finesse dependence of the best-minor slam
mk = [r for r in R if r["mtr"]>=12]
cold = [r for r in mk if r["mtr_sw"]>=12]          # makes both ways -> break/power, not position
pos  = [r for r in mk if r["mtr_sw"]<12]           # flips on swap -> positional (finesse/guess)
# also: fails real but makes swapped (unlucky position) among non-makers
nonmk=[r for r in R if r["mtr"]<12]
gift =[r for r in nonmk if r["mtr_sw"]>=12]

print("\n=== Finesse-dependence of the 6-minor slam ===")
print(f"6-minor makes (DD, real layout):  {100*len(mk)/n:5.1f}%  (n={len(mk)})")
print(f"  of makers, FINESSE-PROOF (makes both ways): {100*len(cold)/max(len(mk),1):5.1f}%")
print(f"  of makers, POSITION/FINESSE-DEPENDENT:      {100*len(pos)/max(len(mk),1):5.1f}%")
print(f"non-makers that WOULD make if position swapped (offside finesse): {100*len(gift)/n:4.1f}% of all")
# realistic slam estimate: cold count for-sure + half of position-dependent (both directions)
realistic = (len(cold) + 0.5*(len(pos)+len(gift)))/n*100
print(f"\nDD 6-minor make rate:        {100*len(mk)/n:5.1f}%")
print(f"Finesse-adjusted estimate:   {realistic:5.1f}%   (cold + 50% of guess-dependent)")

print("\nAvg DD tricks:  bestminor=%.1f  NT=%.1f  C=%.1f  D=%.1f" % (
    st.mean(r['mtr'] for r in R), st.mean(r['NT'] for r in R),
    st.mean(r['C'] for r in R), st.mean(r['D'] for r in R)))

SLAM=lambda r:r["mtr"]>=12
def brk(name, keyfn, order=None):
    print(f"\n--- 6-minor make-rate by {name} ---")
    groups={}
    for r in R: groups.setdefault(keyfn(r),[]).append(r)
    keys=order if order else sorted(groups)
    for k in keys:
        g=groups.get(k,[])
        if g: print(f"  {name}={k}: {100*sum(1 for r in g if SLAM(r))/len(g):5.1f}%   (n={len(g)}, {100*len(g)/n:.0f}% of hands)")

print("\n========== WHEN IS SLAM GOOD vs BAD ==========")
brk("partner HCP", lambda r:r["hcp"])
brk("aces", lambda r:r["aces"])
brk("best-minor fit", lambda r:r["fit"])
brk("fitting honors in trump minor (we have DK / CQJ)", lambda r:r["minor_top"])
brk("wasted spade HCP (opp our stiff)", lambda r:min(r["spade_hcp"],5))
# combined: fit>=8 AND >=3 aces
good=[r for r in R if r["fit"]>=8 and r["aces"]>=3]
bad =[r for r in R if r["fit"]<=7]
print(f"\n  GOOD zone (8+ fit AND 3+ aces): {100*sum(1 for r in good if SLAM(r))/len(good):.0f}%  (n={len(good)}, {100*len(good)/n:.0f}%)")
print(f"  BAD zone  (7-card fit only):    {100*sum(1 for r in bad if SLAM(r))/len(bad):.0f}%  (n={len(bad)}, {100*len(bad)/n:.0f}%)")
mis=[r for r in R if r["fit"]<=7]
print(f"  (7-card fit means partner had only a doubleton in your long minor)")

print("\n=== Examples: FINESSE-PROOF 6-minor makers ===")
for r in cold[:4]:
    print(f"  HCP{r['hcp']} {r['shape']} fit{r['fit']} 6{r['minor']}={r['mtr']}(sw{r['mtr_sw']}) NT={r['NT']}  N: {r['north']}")
print("=== Examples: POSITION/FINESSE-DEPENDENT makers (real makes, swap fails) ===")
for r in pos[:4]:
    print(f"  HCP{r['hcp']} {r['shape']} fit{r['fit']} 6{r['minor']}={r['mtr']}(sw{r['mtr_sw']}) NT={r['NT']}  N: {r['north']}")
