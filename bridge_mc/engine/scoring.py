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
#            level, strain, label — the games either side might buy the hand in.
_GAMES = [(3, "N", "3NT"), (4, "H", "4H"), (4, "S", "4S"),
          (5, "C", "5C"), (5, "D", "5D")]


def sacrifice_deal(us, them, v_us, v_them):
    """One deal's 'bid the save vs pass' equity, from *our* perspective.

    ``us``/``them`` are {strain: DD tricks}. Model: they buy it in their
    best-scoring game; we can bid the cheapest legal save in our best strain
    (doubled); they then take their best counter — double our save, or bid on
    to a major/NT (which we double when beatable). Returns
    (pass_eq, bid_eq, save_label, opp_game_label).
    """
    # Their best game (what we're defending against if we pass).
    opp_lvl = opp_st = None
    opp_score = -10 ** 9
    opp_lab = ""
    for lvl, strn, lab in _GAMES:
        sc = contract_score(lvl, strn, them[strn], v_them, False)
        if sc > opp_score:
            opp_score, opp_lvl, opp_st, opp_lab = sc, lvl, strn, lab
    pass_eq = -opp_score

    best_eq = -10 ** 9
    best_lab = ""
    for scs in ("S", "H", "D", "C", "N"):
        lc = opp_lvl if _RANK[scs] > _RANK[opp_st] else opp_lvl + 1
        if lc > 7:
            continue
        ours = contract_score(lc, scs, us[scs], v_us, True)   # they double our save
        bidon = -10 ** 9                                       # or they bid on
        for ns in ("N", "S", "H"):
            lt2 = lc if _RANK[ns] > _RANK[scs] else lc + 1
            if lt2 > 7:
                continue
            t = them[ns]
            bidon = max(bidon, contract_score(lt2, ns, t, v_them, t < 6 + lt2))
        eq = min(ours, -bidon) if bidon > -10 ** 9 else ours
        if eq > best_eq:
            best_eq = eq
            best_lab = f"{lc}{'NT' if scs == 'N' else scs}"
    return pass_eq, best_eq, best_lab, opp_lab
