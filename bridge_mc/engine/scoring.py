"""Duplicate-bridge contract scoring, including doubled contracts.

Used by the competitive / sacrifice analysis, which needs doubled making and
doubled-undertrick scores that the redeal Contract table (undoubled) doesn't
provide. Score is from the declaring side's perspective.
"""
_PER = {"C": 20, "D": 20, "H": 30, "S": 30, "N": 30}


def contract_score(level, strain, tricks, vul, doubled):
    """Score of a `level`-`strain` contract taking `tricks`, at `vul`, `doubled`."""
    need = 6 + level
    if tricks >= need:
        base = 40 + (level - 1) * 30 if strain == "N" else level * _PER[strain]
        if doubled:
            base *= 2
        s = base + (500 if vul else 300) if base >= 100 else base + 50
        if doubled:
            s += 50                                  # doubled-making insult bonus
        if level == 6:
            s += 750 if vul else 500
        elif level == 7:
            s += 1500 if vul else 1000
        ot = tricks - need
        if ot:
            s += ot * ((200 if vul else 100) if doubled else _PER[strain])
        return s
    u = need - tricks
    if not doubled:
        return -u * (100 if vul else 50)
    if vul:
        return -(200 + (u - 1) * 300)               # 200, 500, 800, ...
    pen = 0
    for i in range(1, u + 1):                        # 100, 300, 500, 800, ...
        pen += 100 if i == 1 else (200 if i <= 3 else 300)
    return -pen


# Bidding rank of a strain within a level (NT highest).
_RANK = {"C": 0, "D": 1, "H": 2, "S": 3, "N": 4}
_GAME_LVL = {"N": 3, "S": 4, "H": 4, "D": 5, "C": 5}


def _label(lvl, st):
    return f"{lvl}{'NT' if st == 'N' else st}"


def _practical_best(tr, vul):
    """The contract a side would buy the hand in: their game if they can make
    one, else their best partscore. -> (score, level, strain, label)."""
    best = (-10 ** 9, 0, None, "")
    for st in ("N", "S", "H", "D", "C"):
        t = tr[st]
        if t < 7:                       # can't make even a 1-level contract
            continue
        gl = _GAME_LVL[st]
        lvl = gl if t >= 6 + gl else t - 6      # game if makeable, else partscore
        sc = contract_score(lvl, st, t, vul, False)
        if sc > best[0]:
            best = (sc, lvl, st, _label(lvl, st))
    return best


def sacrifice_deal(us, them, v_us, v_them):
    """One deal's 'compete/save vs pass' equity, from *our* perspective.

    They buy it in their best makeable contract (their game if they have one,
    otherwise their best partscore). We can bid the cheapest legal contract over
    it in our best strain (doubled); they then double it or bid on (we double
    when beatable). Returns (pass_eq, bid_eq, our_label, opp_label) — a save when
    they're in game, a partscore competition when they're in a partscore.
    """
    opp_score, opp_lvl, opp_st, opp_lab = _practical_best(them, v_them)
    if opp_st is None:                  # they make nothing (very rare)
        return 0.0, 0.0, "", "—"
    pass_eq = -opp_score

    best_eq = -10 ** 9
    best_lab = ""
    for scs in ("S", "H", "D", "C", "N"):
        lc = opp_lvl if _RANK[scs] > _RANK[opp_st] else opp_lvl + 1
        if lc > 7:
            continue
        # They double us only when that's worse for us (i.e. we're going down);
        # if we make it they leave it undoubled.
        ours = min(contract_score(lc, scs, us[scs], v_us, False),
                   contract_score(lc, scs, us[scs], v_us, True))
        bidon = -10 ** 9                                       # or they bid on
        for ns in ("N", "S", "H", "D", "C"):
            lt2 = lc if _RANK[ns] > _RANK[scs] else lc + 1
            if lt2 > 7:
                continue
            t = them[ns]
            bidon = max(bidon, contract_score(lt2, ns, t, v_them, t < 6 + lt2))
        eq = min(ours, -bidon) if bidon > -10 ** 9 else ours
        if eq > best_eq:
            best_eq = eq
            best_lab = _label(lc, scs)
    return pass_eq, best_eq, best_lab, opp_lab
