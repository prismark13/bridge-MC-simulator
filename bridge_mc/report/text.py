"""Plain-text rendering of a :class:`SimResult` for the Log tab / CLI."""
from ..domain.contracts import ORDER


def render_text(result) -> str:
    if result.empty:
        return "No qualifying deals — the constraints may be impossible."

    n, tries = result.accepted, result.tries
    L = []
    want = result.config.n
    if n < want:
        L.append(f"⚠  Only {n} deals in {tries} tries (rare constraint).\n")
    L.append(f"{n} deals   ({tries} tries, {result.accept_rate:.1f}% accepted)")
    L.append(f"double-dummy · analysing {result.side} · "
             f"{'vulnerable' if result.vul else 'non-vul'}")
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
    L.append("\nBIDDING DECISION")
    L.append(f"  best game : {bg.label:<4} EV {bg.avg_score:+.0f}")
    L.append(f"  best slam : {bs.label:<4} EV {bs.avg_score:+.0f}")
    imp = f",  {result.imp:+.2f} IMP/board" if result.imp is not None else ""
    L.append(f"  slam vs game: {result.ev_diff:+.0f} pts{imp}")
    L.append(f"  → {'bid the slam' if result.bid_slam else 'stay in game'}")

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
    return "\n".join(L)
