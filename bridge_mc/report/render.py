"""Render a :class:`SimResult` as a standalone HTML document.

Full modern CSS (flex/grid/variables) — intended for a Chromium view
(QtWebEngine) or a browser. The stylesheet lives in ``assets/report.css`` and
is loaded via importlib.resources so it survives a PyInstaller freeze.
"""
from __future__ import annotations

import re
from functools import lru_cache
from importlib.resources import files

from ..domain.contracts import ORDER, SUIT_SYM, SUITS, VUL_LABEL, is_game
from ..domain.decision import decide
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


def _len_label(mn, mx, sym):
    if mn == 0 and mx >= 13:
        return ""
    if mn == mx:
        return f"{mn}{sym}"
    if mx >= 13:
        return f"{mn}+{sym}"
    if mn == 0:
        return f"≤{mx}{sym}"
    return f"{mn}-{mx}{sym}"


def _honor_desc(sp):
    bits = []
    for suit, named, xc in sp.holdings:
        bits.append(f'{"".join(named)}{"x" * xc}{SUIT_SYM[suit]}')
    for suit, n, m in sp.tops:
        bits.append(f'{n} of top {m}{SUIT_SYM[suit]}')
    if sp.ctrl_lo > 0 or sp.ctrl_hi < 12:
        bits.append(f'{sp.ctrl_lo}+ ctrl' if sp.ctrl_hi >= 12
                    else f'{sp.ctrl_lo}-{sp.ctrl_hi} ctrl')
    return " · ".join(bits)


_LABIFY_RE = re.compile(r"(?<!\w)([1-7])(NT|[SHDC])(?!\w)")


def _labify(text):
    """Suit-symbolise contract labels (6H -> 6♥, 3NT stays) inside prose."""
    return _LABIFY_RE.sub(lambda m: _lab(m.group(0)), text)


def _seat_spec(sp):
    if sp.kind == "fixed":
        return "Fixed", _hand_html(_symify(sp.fixed)), ""
    if sp.kind == "con":
        if sp.shape == "minlen":
            shp = " ".join(_len_label(sp.mins[i], sp.maxs[i], SUIT_SYM[SUITS[i]])
                           for i in range(4)).strip() or "any shape"
        else:
            shp = {"any": "any shape", "bal": "balanced",
                   "semibal": "semi-balanced"}[sp.shape]
        hon = _honor_desc(sp)
        meta = f"{shp} · {hon}" if hon and shp != "any shape" else (hon or shp)
        return "Constrained", f"{sp.lo}–{sp.hi} HCP", meta
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


def _competitive_html(r):
    def col(title, vulnerable, bg, bs):
        vb = ' <span class="vul">VUL</span>' if vulnerable else ''
        return (f'<div class="cside"><p class="k">{title}{vb}</p>'
                f'<div class="cmp">best game <b>{_lab(bg.label)}</b> '
                f'<span class="mp">{bg.make_rate:.0f}%</span>'
                f'<span class="se"> · EV {bg.avg_score:+.0f}</span></div>'
                f'<div class="cmp">best slam <b>{_lab(bs.label)}</b> '
                f'<span class="mp">{bs.make_rate:.0f}%</span>'
                f'<span class="se"> · EV {bs.avg_score:+.0f}</span></div></div>')
    us = col(f"Us · {r.side}", r.vul_us, r.best_game, r.best_slam)
    them = col(f"Them · {r.opp_side}", r.vul_them, r.opp_best_game, r.opp_best_slam)
    par = ""
    if r.par:
        tops = " · ".join(f'{_par_contract(c)} <span class="se">×{n}</span>'
                          for c, n in r.par.top)
        par = (f'<div class="par"><span class="k">Par · optimal competitive result '
               f'({r.side})</span>'
               f'<span class="pv">{r.par.avg_us:+.0f}</span>'
               f'<span class="se">avg score · sacrifice is par on '
               f'{r.par.sac_rate*100:.0f}% of boards</span>'
               f'<div class="ptop">{tops}</div></div>')
    return f"""
  <section>
    <p class="kicker">Competitive picture · both sides at this vulnerability</p>
    <h2>What each side can make</h2>
    <p class="sec-lead">Board vulnerability <b>{VUL_LABEL[r.vul]}</b>. Each side's contracts are
       scored at its own vulnerability. <b>Par</b> is the double-dummy-optimal competitive
       result — what the auction settles at with best bidding, doubled sacrifices included.</p>
    <div class="two-col">{us}{them}</div>
    {par}
  </section>"""


_PAR_TOK = re.compile(r"^(\d)([SHDCN])(x*)([-+=]?\d*)$")


def _par_token(tok):
    m = _PAR_TOK.match(tok)
    if not m:
        return tok
    lvl, den, dbl, res = m.groups()
    sym = {"S": "♠", "H": "♥", "D": "♦", "C": "♣", "N": "NT"}[den]
    cls = ' class="pip"' if den in "HD" else ""
    return f'{lvl}<span{cls}>{sym}</span>{"×" if dbl else ""}{res}'


def _par_contract(c):
    """'EW 5Dx,EW 5Cx' -> 'EW 5♦× / EW 5♣×' — split packed alternatives, colour suits."""
    alts = []
    for chunk in c.split(","):
        alts.append(" ".join(_par_token(t) for t in chunk.split()))
    return " / ".join(a for a in alts if a)


def _finesse_html(r):
    if not r.finesse:
        if r.finesse_note:
            return (f'  <section><p class="kicker">Card placement · finesse</p>'
                    f'<p class="sec-lead">{r.finesse_note}</p></section>')
        return ""
    seen, picks = set(), []
    for c in (r.best_game, r.best_slam, r.best_grand):
        if c and c.label not in seen and c.make_rate >= 8:
            seen.add(c.label); picks.append(c)
    if not picks:
        return ""

    def row(c):
        return (f'<tr><td class="lab">{_lab(c.label)}</td>'
                f'<td class="pct">{c.make_rate:.0f}% '
                f'<span class="se">({c.proof_rate:.0f}% floor)</span></td>'
                f'<td>{_bar(c.proof_rate)}</td>'
                f'<td class="sc">{c.sens_rate:.0f}%</td></tr>')
    rows = "".join(row(c) for c in picks)
    return f"""
  <section>
    <p class="kicker">Card placement · finesse dependence</p>
    <h2>How much of it hinges on where the cards sit</h2>
    <p class="sec-lead">Each deal is re-solved with the defenders swapped. The make-rate shows
       <b>double-dummy % (single-dummy floor %)</b> — the floor is <b>position-proof</b>: makes
       however the defenders' cards lie. <b>Hinges</b> is the slice that flips on the swap — a
       finesse/endplay you'd have to read. (A pure two-way guess reads as position-proof, so the
       floor is optimistic for those, not a strict single-dummy number.)</p>
    <div class="tbl-wrap"><table>
      <thead><tr><th>Contract</th><th>DD % (floor %)</th><th>&nbsp;</th>
        <th style="text-align:right">Hinges</th></tr></thead>
      <tbody>{rows}</tbody></table></div>
  </section>"""


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


def _tile(k, v, sub, color=None):
    style = f' style="color:{color}"' if color else ""
    return (f'<div class="tile"><p class="k">{k}</p>'
            f'<div class="v"{style}>{v}</div><div class="sub">{sub}</div></div>')


def _tiles(r, competitive, tone, game_pct, slam_pct, ev_diff):
    bg, bs, og = r.best_game, r.best_slam, r.opp_best_game
    if competitive:
        s = r.sacrifice
        stone = "var(--good)" if (s and s.recommend_bid) else "var(--warn)"
        t = _tile(f'They make <span class="tag">{_lab(og.label)}</span>',
                  f'{og.make_rate:.0f}<i>%</i>', "opponents' best game")
        t += _tile('Par · you', f'{r.par.avg_us:+.0f}' if r.par else '—',
                   'optimal competitive result',
                   "var(--good)" if (r.par and r.par.avg_us > 0) else "var(--warn)")
        if s and s.save_bid:
            act = "Save" if is_game(s.opp_game) else "Compete"
            t += _tile(f'{act} {_lab(s.save_bid)} vs pass',
                       'Bid' if s.recommend_bid else 'Pass',
                       f'by {abs(s.avg_bid - s.avg_pass):.0f} pts/board', stone)
            t += _tile(f'{act} beats pass', f'{s.bid_better * 100:.0f}<i>%</i>',
                       'of deals')
        return t
    t = _tile(f'Best game <span class="tag">{_lab(bg.label)}</span>',
              f'{game_pct:.1f}<i>%</i>', 'makes double-dummy')
    t += _tile(f'Best slam <span class="tag">{_lab(bs.label)}</span>',
               f'{slam_pct:.1f}<i>%</i>', 'makes double-dummy')
    t += _tile('Slam &minus; game', f'{ev_diff:+.0f}', 'points, expected', tone)
    gr = r.best_grand
    if gr and gr.make_rate >= 20:
        gtone = "var(--good)" if r.bid_grand else None
        sub = (f'+{gr.avg_score - bs.avg_score:.0f} pts vs the small slam'
               if r.bid_grand else 'small slam is enough')
        t += _tile(f'Grand <span class="tag">{_lab(gr.label)}</span>',
                   f'{gr.make_rate:.0f}<i>%</i>', sub, gtone)
    elif r.imp is not None:
        t += _tile('Slam swing', f'{r.imp:+.2f}', 'IMP / board', tone)
    else:
        t += _tile('Grand slam', f'{r.grand.make_rate:.0f}<i>%</i>',
                   '13 tricks available')
    return t


def _sacrifice_html(r):
    s = r.sacrifice
    if not s or not s.save_bid:
        return ""
    stone = "var(--good)" if s.recommend_bid else "var(--warn)"
    verdict = f"Bid {_lab(s.save_bid)}" if s.recommend_bid else "Pass"
    save = _lab(s.save_bid)
    opp = _lab(s.opp_game) if s.opp_game else "their contract"
    # A save when they're in a game; a partscore competition otherwise.
    save_over_game = is_game(s.opp_game)
    word = "Sacrifice" if save_over_game else "Competitive"
    kind = "sacrifice" if save_over_game else "overcall"
    return f"""
  <section>
    <p class="kicker">{word} decision · {r.side}: bid {save} or pass</p>
    <h2>{'Save' if save_over_game else 'Compete'} over {opp}, or pass?</h2>
    <p class="sec-lead">Average equity to {r.side} of always choosing each action, at
       {VUL_LABEL[r.vul]} vulnerability — the opponents double or bid on optimally.</p>
    <div class="two-col">
      <div class="cside"><p class="k">Pass — let {r.opp_side} play {opp}</p>
        <span class="pv">{s.avg_pass:+.0f}</span><span class="se">average equity</span></div>
      <div class="cside"><p class="k">Bid {save} ({kind})</p>
        <span class="pv" style="color:{stone}">{s.avg_bid:+.0f}</span>
        <span class="se">average equity · beats passing on {s.bid_better * 100:.0f}% of deals</span></div>
    </div>
    <div class="par"><span class="k">Verdict</span>
      <span class="pv" style="color:{stone}">{verdict}</span>
      <span class="se">better by {abs(s.avg_bid - s.avg_pass):.0f} points/board on average</span></div>
  </section>"""


def _auction_calls(auction: str) -> str:
    """Pretty-print raw calls: '1D P 1H X' -> '1♦ Pass 1♥ Dbl'."""
    out = []
    for tok in auction.split():
        t = tok.upper()
        if t in ("P", "PASS"):
            out.append("Pass")
        elif t in ("X", "DBL"):
            out.append("Dbl")
        elif t in ("XX", "RDBL"):
            out.append("Rdbl")
        else:
            out.append(_labify(t))
    return "&nbsp; ".join(out)


def _auction_html(r):
    a = r.auction
    if not a:
        return ""
    con = _lab(a.contract)
    dbl = {0: "", 1: " ×", 2: " ××"}[a.doubled]
    lead = (f'<p class="sec-lead">Dealer {r.config.dealer}. '
            f'<span class="mono">{_auction_calls(r.config.auction)}</span></p>')
    if not a.on_our_side:
        beat = 100 - a.dec_rate
        return f"""
  <section>
    <p class="kicker">The auction · their contract</p>
    <h2>{con}{dbl} by {a.declarer} makes {a.dec_rate:.0f}%</h2>
    {lead}
    <p class="sec-lead">{a.side} play <b>{con}{dbl}</b> ({a.declarer}). Here is how
       often it comes home against your two hands, over random layouts for the other
       defender.</p>
    <div class="two-col">
      <div class="cside"><p class="k">{a.side} make {con}{dbl}</p>
        <span class="pv" style="color:var(--warn)">{a.dec_rate:.0f}%</span>
        <span class="se">their contract comes home</span></div>
      <div class="cside"><p class="k">You beat it</p>
        <span class="pv" style="color:var(--good)">{beat:.0f}%</span>
        <span class="se">set {con} at least one trick</span></div>
    </div>
  </section>"""
    tone = "var(--warn)" if a.wrong_side else "var(--good)"
    if a.wrong_side:
        verdict = (f"Wrong side — {con} is stuck in {a.declarer}'s hand, "
                   f"{abs(a.swing):.0f} points of make-rate worse than if {a.partner} "
                   f"could play it. The auction can't get the lead coming up to the tenaces.")
    elif a.swing > 3:
        verdict = (f"Right side — {a.declarer} is the better declarer for {con} "
                   f"by {abs(a.swing):.0f} points.")
    else:
        verdict = f'Either hand plays {con} about equally — declarer barely matters here.'
    return f"""
  <section>
    <p class="kicker">The auction · who declares</p>
    <h2>Your auction makes {a.declarer} the declarer of {con}{dbl}</h2>
    {lead}
    <div class="two-col">
      <div class="cside"><p class="k">As played — {a.declarer} declares</p>
        <span class="pv" style="color:{tone}">{a.dec_rate:.0f}%</span>
        <span class="se">the reachable make-rate</span></div>
      <div class="cside"><p class="k">If {a.partner} could declare</p>
        <span class="pv">{a.par_rate:.0f}%</span>
        <span class="se">not reachable in this auction</span></div>
    </div>
    <div class="par"><span class="k">Declarer swing</span>
      <span class="pv" style="color:{tone}">{a.swing:+.0f}%</span>
      <span class="se">{verdict}</span></div>
  </section>"""


def _esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _answer_html(question, answer):
    """A prominent banner at the top of the report: the user's question and
    Claude's answer (or a pending state while it streams)."""
    if not question:
        return ""
    if answer:
        body = f'<div class="aa">{_labify(_esc(answer))}</div>'
    else:
        body = '<div class="aa pending">Asking Claude&hellip;</div>'
    return (f'<section class="answer"><p class="kicker">\U0001F9E0 Your question</p>'
            f'<p class="aq">{_esc(question)}</p>{body}</section>')


ALL_SECTIONS = ("tiles", "auction", "hands", "tables", "competitive",
                "finesse", "breakdown", "samples")


def render_html(result, theme: str = "light", answer=None, question=None,
                show=None) -> str:
    show = ALL_SECTIONS if show is None else set(show)
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
    opp = result.opp_side
    vul = f"vul {VUL_LABEL[result.vul]}"
    bg, bs = result.best_game, result.best_slam
    ev_diff = result.ev_diff
    slam_pct, game_pct = bs.make_rate, bg.make_rate

    # One Decision drives the hero + which pane leads (see domain/decision.py).
    d = decide(result)
    competitive = d.axis == "competitive"
    tone = f"var(--{d.tone})"
    hero_pct = d.headline
    h1 = f"{d.verb} {_lab(d.contract)}"
    say = _labify(d.detail)
    conf_badge = ""
    if d.confidence:
        ct = {"High": "good", "Medium": "muted", "Low": "warn"}[d.confidence]
        conf_badge = (f'<div class="conf conf-{ct}">Confidence <b>{d.confidence}</b>'
                      f' &middot; {d.solidity:.0f}% of {_lab(d.contract)} is position-proof; '
                      f'the rest rides on card placement</div>')

    tiles = _tiles(result, competitive, tone, game_pct, slam_pct, ev_diff)

    cards = ""
    for seat in ORDER:
        tag, main, meta = _seat_spec(specs.get(seat, SeatSpec.random()))
        me = " me" if seat in side else ""
        cards += (f'<div class="card{me}"><p class="seat"><span>{seat}</span>'
                  f'<span>{tag}</span></p><div class="spec">{main}</div>'
                  f'{f"<div class=meta>{meta}</div>" if meta else ""}</div>')

    game_rows = "".join(_row(s) for s in result.games) + _row(result.any_game)
    slam_rows = "".join(_row(s) for s in result.slams) + _row(result.any_slam)
    grand_rows = "".join(_row(s) for s in result.grands) + _row(result.grand)

    samples = ""
    if result.samples:
        egs = ""
        for sd in result.samples:
            hh = "".join(f'<div>{seat}&nbsp; {_hand_html(sd.hands[seat])}</div>'
                         for seat in ORDER)
            st = sd.tricks
            tr = (f'♣{st["C"]} <span class="pip">♦{st["D"]} ♥{st["H"]}</span> '
                  f'♠{st["S"]} NT{st["N"]}')
            parline = (f'<div class="t">par &nbsp;{_par_contract(sd.par)} '
                       f'<span class="se">({sd.par_score:+d})</span></div>'
                       if sd.par else "")
            egs += (f'<div class="eg"><div class="h">{hh}</div>'
                    f'<div class="t">DD tricks &nbsp;{tr}</div>{parline}</div>')
        samples = ('<section><p class="kicker">Representative deals</p>'
                   '<h2>Sample layouts from the run</h2>'
                   f'<div class="egs">{egs}</div></section>')

    # Each section is opt-in via ``show`` so the app can toggle them on/off.
    sec_tiles = f"""
  <section>
    <p class="kicker">{"The picture at a glance" if competitive else f"Contract success · {side}, realistic declarer"}</p>
    <h2>{"Their hand — your options" if competitive else f"How often {side}'s contracts make"}</h2>
    <div class="tiles">{tiles}</div>
  </section>""" if "tiles" in show else ""
    sec_auction = _auction_html(result) if "auction" in show else ""
    sec_hands = f"""
  <section>
    <p class="kicker">The deal setup</p>
    <h2>What was dealt</h2>
    <div class="hand-grid">{cards}</div>
  </section>""" if "hands" in show else ""
    sec_tables = f"""
  <section>
    <p class="kicker">Make-rates with 95% confidence intervals</p>
    <h2>{"Your games" if competitive else f"Every game &amp; slam for {side}"}</h2>
    <div class="tbl-wrap">
      <p class="gcap">Games</p>
      <table><thead><tr><th>Contract</th><th>Make-rate</th><th>&nbsp;</th><th style="text-align:right">Avg score</th></tr></thead>
        <tbody>{game_rows}</tbody></table>
      {"" if competitive else f'<p class="gcap" style="margin-top:22px">Slams</p><table><tbody>{slam_rows}</tbody></table><p class="gcap" style="margin-top:22px">Grands</p><table><tbody>{grand_rows}</tbody></table>'}
    </div>
  </section>""" if "tables" in show else ""
    sec_comp = _competitive_html(result) if "competitive" in show else ""
    sec_sac = (_sacrifice_html(result)
               if ("competitive" in show and competitive and not result.we_own) else "")
    sec_finesse = _finesse_html(result) if "finesse" in show else ""
    sec_breakdown = _breakdown_html(result.breakdown) if "breakdown" in show else ""
    sec_samples = samples if "samples" in show else ""

    return head + f"""
  {_answer_html(question, answer)}
  <header class="hero">
    <p class="eyebrow">Double-dummy Monte Carlo · {n:,} deals</p>
    <h1>{h1}</h1>
    <p class="lede">Fixed hands held constant; the constrained seat is dealt every layout
       that fits, and the opponents get the rest. Each deal is solved for perfect play,
       analysing {side} vs {opp}, {vul}.</p>
    <div class="verdict" style="--tone:{tone}">
      <b>{hero_pct:.0f}<i>%</i></b>
      <span class="say">{say}</span>
    </div>
    {conf_badge}
  </header>
  {sec_tiles}
  {sec_auction}
  {sec_hands}
  {sec_tables}
  {sec_comp}
  {sec_sac}
  {sec_finesse}
  {sec_breakdown}
  {sec_samples}
  <footer class="foot">
    <div class="method">
      <span>ENGINE · bundled DDS (Bo Haglund)</span>
      <span>DEALS · {n:,} of {tries:,} tries · {result.accept_rate:.1f}% accepted</span>
      <span>DECLARER · suit = long-trump hand · NT = better hand</span>
      <span>ANALYSING · {side} vs {opp} · {vul}</span>
    </div>
    <b>Double-dummy (perfect-play) figures.</b> Real single-dummy results typically run a few
    percent lower for slams. Make-rates carry a 95% confidence interval from the sample size.
  </footer>
""" + tail
