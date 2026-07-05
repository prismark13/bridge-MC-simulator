"""Render a :class:`SimResult` as a standalone HTML document.

Full modern CSS (flex/grid/variables) — intended for a Chromium view
(QtWebEngine) or a browser. The stylesheet lives in ``assets/report.css`` and
is loaded via importlib.resources so it survives a PyInstaller freeze.
"""
from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

from ..domain.contracts import ORDER, SUIT_SYM, SUITS
from ..domain.types import SeatSpec

_SUIT_SYM4 = ["♠", "♥", "♦", "♣"]


@lru_cache(maxsize=1)
def _css() -> str:
    return files("bridge_mc.report").joinpath("assets/report.css").read_text(encoding="utf-8")


def _symify(hstr):
    return " ".join(sym + (g if g != "-" else "—")
                    for sym, g in zip(_SUIT_SYM4, hstr.split()))


def _hand_html(sym_str):
    out = []
    for tok in sym_str.split():
        cls = ' class="pip"' if tok[:1] in ("♥", "♦") else ""
        out.append(f"<span{cls}>{tok}</span>")
    return " ".join(out)


def _lab(label):
    """'6D' -> 6♦ (red), '3NT' -> 3NT, '4S' -> 4♠, passthrough for words."""
    if label.endswith("NT"):
        return label
    if len(label) >= 2 and label[:-1].isdigit():
        sym = {"C": "♣", "D": "♦", "H": "♥", "S": "♠"}.get(label[-1])
        if sym:
            cls = ' class="pip"' if label[-1] in "HD" else ""
            return f"{label[:-1]}<span{cls}>{sym}</span>"
    return label


def _seat_spec(sp):
    if sp.kind == "fixed":
        return "Fixed", _hand_html(_symify(sp.fixed)), ""
    if sp.kind == "con":
        if sp.shape == "minlen":
            shp = " ".join(f"{m}+{SUIT_SYM[SUITS[i]]}"
                           for i, m in enumerate(sp.mins) if m) or "any shape"
        else:
            shp = {"any": "any shape", "bal": "balanced",
                   "semibal": "semi-balanced"}[sp.shape]
        return "Constrained", f"{sp.lo}–{sp.hi} HCP", shp
    return "Random", "—", ""


def _bar(p):
    soft = " soft" if p < 55 else ""
    return (f'<div class="meter"><div class="track">'
            f'<div class="fill{soft}" style="width:{p:.0f}%"></div></div></div>')


def _row(stat):
    p = stat.make_rate
    av = f"{stat.avg_score:+.0f}" if stat.avg_score is not None else "—"
    return (f'<tr><td class="lab">{_lab(stat.label)}</td>'
            f'<td class="pct">{p:.1f}%<span class="se"> ±{stat.ci95:.1f}</span></td>'
            f'<td>{_bar(p)}</td><td class="sc">{av}</td></tr>')


def _slice_rows(stats):
    return "".join(
        f'<tr><td class="lab" style="width:auto">{s.label}</td>'
        f'<td class="pct">{s.make_rate:.0f}%<span class="se"> ±{s.ci95:.0f}</span></td>'
        f'<td>{_bar(s.make_rate)}</td>'
        f'<td class="sc">n={s.trials}</td></tr>' for s in stats)


def _breakdown_html(bd):
    if not bd or not bd.by_hcp:
        return ""
    cols = [("By partner HCP", bd.by_hcp)]
    if bd.by_trump:
        cols.append(("By trump length", bd.by_trump))
    if bd.by_short:
        cols.append(("By short-suit (ruffing)", bd.by_short))
    blocks = "".join(
        f'<div><p class="gcap">{cap}</p><table><tbody>{_slice_rows(st)}</tbody></table></div>'
        for cap, st in cols)
    c = _lab(bd.contract_label)
    return f"""
  <section>
    <p class="kicker">When {c} makes · {bd.focus_seat}'s hand</p>
    <h2>Which {bd.focus_seat} hands should bid on</h2>
    <p class="sec-lead">{c} make-rate sliced by the constrained hand. Extra strength,
       longer trumps, and a short side-suit each push {c} from a guess toward a make —
       so bid on with those, sign off without them.</p>
    <div class="bd-grid">{blocks}</div>
  </section>"""


def render_html(result, theme: str = "light") -> str:
    specs = result.config.specs if result.config else {}
    head = (f'<!doctype html><html data-theme="{theme}"><head><meta charset="utf-8">'
            f'<title>Bridge MC report</title><style>{_css()}</style></head><body>'
            f'<div class="page"><div class="wrap">')
    tail = "</div></div></body></html>"

    if result.empty:
        return (head + '<div class="hero"><p class="eyebrow">Double-dummy Monte Carlo</p>'
                '<h1>No qualifying deals</h1><p class="lede">The constraints may be '
                'impossible — loosen the HCP range or the shape and run again.</p></div>' + tail)

    side, n, tries = result.side, result.accepted, result.tries
    vul = "vulnerable" if result.vul else "non-vulnerable"
    bg, bs = result.best_game, result.best_slam
    ev_diff = result.ev_diff
    bid_slam = result.bid_slam
    tone = "var(--good)" if bid_slam else "var(--warn)"
    slam_pct, game_pct = bs.make_rate, bg.make_rate
    imp_txt = f" · {result.imp:+.2f} IMP/board" if result.imp is not None else ""

    h1 = (f"Bid the slam &mdash; {_lab(bs.label)}" if bid_slam
          else f"Game is the limit &mdash; {_lab(bg.label)}")
    say = (f'6-level {_lab(bs.label)} makes this often, and it beats the best game '
           f'by <span class="em">{ev_diff:+.0f} pts{imp_txt}</span>.' if bid_slam
           else f'The best game {_lab(bg.label)} is the ceiling — the slam '
                f'<span class="em">loses {ev_diff:+.0f} pts</span> to it.')

    tiles = (
        f'<div class="tile"><p class="k">Best game <span class="tag">{_lab(bg.label)}</span></p>'
        f'<div class="v">{game_pct:.1f}<i>%</i></div><div class="sub">makes double-dummy</div></div>'
        f'<div class="tile"><p class="k">Best slam <span class="tag">{_lab(bs.label)}</span></p>'
        f'<div class="v">{slam_pct:.1f}<i>%</i></div><div class="sub">makes double-dummy</div></div>'
        f'<div class="tile"><p class="k">Slam &minus; game</p>'
        f'<div class="v" style="color:{tone}">{ev_diff:+.0f}</div><div class="sub">points, expected</div></div>')
    if result.imp is not None:
        tiles += (f'<div class="tile"><p class="k">Slam swing</p>'
                  f'<div class="v" style="color:{tone}">{result.imp:+.2f}</div>'
                  f'<div class="sub">IMP / board</div></div>')
    else:
        tiles += (f'<div class="tile"><p class="k">Grand slam</p>'
                  f'<div class="v">{result.grand.make_rate:.0f}<i>%</i></div>'
                  f'<div class="sub">13 tricks available</div></div>')

    cards = ""
    for seat in ORDER:
        tag, main, meta = _seat_spec(specs.get(seat, SeatSpec.random()))
        me = " me" if seat in side else ""
        cards += (f'<div class="card{me}"><p class="seat"><span>{seat}</span>'
                  f'<span>{tag}</span></p><div class="spec">{main}</div>'
                  f'{f"<div class=meta>{meta}</div>" if meta else ""}</div>')

    game_rows = "".join(_row(s) for s in result.games) + _row(result.any_game)
    slam_rows = "".join(_row(s) for s in result.slams) + \
        _row(result.any_slam) + _row(result.grand)

    samples = ""
    if result.samples:
        egs = ""
        for sd in result.samples:
            hh = "".join(f'<div>{seat}&nbsp; {_hand_html(sd.hands[seat])}</div>'
                         for seat in ORDER)
            st = sd.tricks
            tr = (f'♣{st["C"]} <span class="pip">♦{st["D"]} ♥{st["H"]}</span> '
                  f'♠{st["S"]} NT{st["N"]}')
            egs += f'<div class="eg"><div class="h">{hh}</div><div class="t">DD tricks &nbsp;{tr}</div></div>'
        samples = ('<section><p class="kicker">Representative deals</p>'
                   '<h2>Sample layouts from the run</h2>'
                   f'<div class="egs">{egs}</div></section>')

    return head + f"""
  <header class="hero">
    <p class="eyebrow">Double-dummy Monte Carlo · {n:,} deals</p>
    <h1>{h1}</h1>
    <p class="lede">Fixed hands held constant; the constrained seat is dealt every layout
       that fits, and the opponents get the rest. Each deal is solved for perfect play,
       analysing {side} {vul}.</p>
    <div class="verdict" style="--tone:{tone}">
      <b>{slam_pct:.0f}<i>%</i></b>
      <span class="say">{say}</span>
    </div>
  </header>

  <section>
    <p class="kicker">Contract success · better of the two declarers</p>
    <h2>Games are safe; the slam is the swing decision</h2>
    <div class="tiles">{tiles}</div>
  </section>

  <section>
    <p class="kicker">The deal setup</p>
    <h2>What was dealt</h2>
    <div class="hand-grid">{cards}</div>
  </section>

  <section>
    <p class="kicker">Make-rates with 95% confidence intervals</p>
    <h2>Every game &amp; slam for {side}</h2>
    <div class="tbl-wrap">
      <p class="gcap">Games</p>
      <table><thead><tr><th>Contract</th><th>Make-rate</th><th>&nbsp;</th><th style="text-align:right">Avg score</th></tr></thead>
        <tbody>{game_rows}</tbody></table>
      <p class="gcap" style="margin-top:22px">Slams</p>
      <table><tbody>{slam_rows}</tbody></table>
    </div>
  </section>

  {_breakdown_html(result.breakdown)}

  {samples}

  <footer class="foot">
    <div class="method">
      <span>ENGINE · bundled DDS (Bo Haglund)</span>
      <span>DEALS · {n:,} of {tries:,} tries · {result.accept_rate:.1f}% accepted</span>
      <span>ANALYSING · {side} · {vul}</span>
    </div>
    <b>Double-dummy (perfect-play) figures.</b> Real single-dummy results typically run a few
    percent lower for slams. Make-rates carry a 95% confidence interval from the sample size.
  </footer>
""" + tail
