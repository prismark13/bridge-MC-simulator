"""Plain-text rendering of a :class:`SimResult` for the Log tab / CLI."""
from ..domain.contracts import ORDER, VUL_LABEL


def render_text(result) -> str:
    if result.empty:
        return "No qualifying deals — the constraints may be impossible."

    n, tries = result.accepted, result.tries
    L = []
    want = result.config.n
    if n < want:
        L.append(f"⚠  Only {n} deals in {tries} tries (rare constraint).\n")
    L.append(f"{n} deals   ({tries} tries, {result.accept_rate:.1f}% accepted)")
    L.append(f"double-dummy · {result.side} vs {result.opp_side} · "
             f"vul {VUL_LABEL[result.vul]}"
             f"  (us {'V' if result.vul_us else '-'}, them {'V' if result.vul_them else '-'})")
    L.append(f"\n{'':<10}{'make':^12}{'avg':>7}")

    def row(stat):
        av = f"{stat.avg_score:+6.0f}" if stat.avg_score is not None else "      "
        L.append(f"  {stat.label:<8}{stat.make_rate:5.1f}% ±{stat.ci95:3.1f}  "
                 f"{av}  {'▉' * round(stat.make_rate / 5)}")

    L.append("GAMES")
    for s in result.games:
        row(s)
    row(result.any_game)
    L.append("SLAMS")
    for s in result.slams:
        row(s)
    row(result.any_slam)
    row(result.grand)

    bg, bs = result.best_game, result.best_slam
    og = result.opp_best_game
    competitive = og is not None and max(bg.make_rate, bs.make_rate) < 50 \
        and og.make_rate >= 50
    L.append("\nBIDDING DECISION")
    L.append(f"  best game : {bg.label:<4} {bg.make_rate:4.0f}%  EV {bg.avg_score:+.0f}")
    L.append(f"  best slam : {bs.label:<4} {bs.make_rate:4.0f}%  EV {bs.avg_score:+.0f}")
    imp = f",  {result.imp:+.2f} IMP/board" if result.imp is not None else ""
    L.append(f"  slam vs game: {result.ev_diff:+.0f} pts{imp}")
    if competitive:
        L.append(f"  → competitive deal: {result.opp_side} own it "
                 f"({og.label} {og.make_rate:.0f}%) — compete / sacrifice / defend (see PAR)")
    else:
        L.append(f"  → {'bid the slam' if result.bid_slam else 'stay in game'}")

    og, os_ = result.opp_best_game, result.opp_best_slam
    if og and os_:
        vt = " (vulnerable)" if result.vul_them else ""
        L.append(f"\nOPPONENTS  {result.opp_side}{vt}")
        L.append(f"  best game : {og.label:<4} {og.make_rate:4.0f}%  EV {og.avg_score:+.0f}")
        L.append(f"  best slam : {os_.label:<4} {os_.make_rate:4.0f}%  EV {os_.avg_score:+.0f}")

    p = result.par
    if p:
        L.append(f"\nPAR  (optimal competitive result, {result.side} view)")
        L.append(f"  avg par score : {p.avg_us:+.0f}")
        L.append(f"  sacrifice is par on {p.sac_rate*100:.0f}% of boards")
        if p.top:
            L.append("  common par    : " +
                     ", ".join(f"{c} ({n})" for c, n in p.top))

    s = result.sacrifice
    if s and s.save_bid and result.zone == "competitive":
        L.append(f"\nSACRIFICE  ({result.side}: bid {s.save_bid} vs pass over {s.opp_game})")
        L.append(f"  always pass : avg {s.avg_pass:+.0f}")
        L.append(f"  always bid  : avg {s.avg_bid:+.0f}   (beats pass {s.bid_better*100:.0f}%)")
        L.append(f"  → {'BID the save' if s.recommend_bid else 'PASS'} "
                 f"(by {abs(s.avg_bid-s.avg_pass):.0f}/board)")

    bd = result.breakdown
    if bd and bd.by_hcp:
        L.append(f"\nWHEN {bd.contract_label} MAKES  (by {bd.focus_seat}'s hand)")

        def line(title, stats):
            L.append(f"  {title:<10}" +
                     "   ".join(f"{s.label} {s.make_rate:.0f}%" for s in stats))
        line("by HCP", bd.by_hcp)
        if bd.by_trump:
            line("by trumps", bd.by_trump)
        if bd.by_short:
            line("by shape", bd.by_short)

    if result.samples:
        L.append("\nSAMPLE DEALS")
        for sd in result.samples:
            for seat in ORDER:
                L.append(f"  {seat} {sd.hands[seat]}")
            st = sd.tricks
            L.append(f"    tricks  C{st['C']} D{st['D']} H{st['H']} "
                     f"S{st['S']} NT{st['N']}")
            if sd.par:
                L.append(f"    par     {sd.par}  ({sd.par_score:+d})")
    return "\n".join(L)
