"""Claude 'Explain' verdict.

Two modes, chosen by whether the user typed a question in the Ask box:

* **question mode** — answer exactly that, leading with the number. When the
  question names a contract (including partscores the standard stats omit, like
  ``3D`` or ``3Dx by West``), its make-rate is resolved from the DD trick
  distribution and injected, so Claude answers from real figures, not a guess.
* **default mode** — the standard bid/stop verdict driven by ``decide()``.

``build_prompt`` is pure (SimResult, question -> str). ``stream_explanation`` is
the only place that touches the anthropic SDK. One system prompt covers both
modes; it switches on the presence of a ``QUESTION`` line.
"""
import re

from ..domain.decision import decide
from ..domain.contracts import ORDER

try:
    import anthropic
    HAVE_ANTHROPIC = True
except Exception:
    HAVE_ANTHROPIC = False

MODEL = "claude-opus-4-8"
SYSTEM = (
    "You are a sharp bridge analyst reading a double-dummy Monte-Carlo "
    "simulation. If the prompt contains a line beginning 'QUESTION', answer ONLY "
    "that question: lead with the number/answer in your first sentence, then at "
    "most one or two sentences giving the single biggest reason. Do NOT mention "
    "games, slams, par, or any contract the question did not ask about, and do "
    "not restate the question. Otherwise, give the standard verdict: recommend "
    "one action decisively with the one or two numbers that drive it, name the "
    "safety net, and for a competitive decision weigh compete / double / "
    "sacrifice; keep it ~120-180 words. In both modes use ONLY the figures "
    "provided, plain text, no preamble."
)

_SEATWORD = {"north": "N", "south": "S", "east": "E", "west": "W"}
_SUITMAP = {"club": "C", "diamond": "D", "heart": "H", "spade": "S"}
# compact contract tokens: 3D, 3NT, 4Sx, 6n ...
_CONTRACT_RE = re.compile(r"([1-7])\s*(nt|n|s|h|d|c)(x{0,2})\b", re.I)
# spelled-out: "3 diamonds", "4 spades", "3 notrump"
_SUIT_RE = re.compile(
    r"([1-7])\s*(clubs?|diamonds?|hearts?|spades?|no[-\s]?trumps?|nt)\b", re.I)


def _seat(specs, s):
    sp = specs.get(s)
    if sp is None or sp.kind == "random":
        return f"{s}: unknown (dealt at random)"
    if sp.kind == "fixed":
        return f"{s}: {sp.fixed}"
    if sp.shape == "minlen":
        def rng(mn, mx):
            if mn == mx:
                return str(mn)
            if mx >= 13:
                return f"{mn}+"
            return f"0-{mx}" if mn == 0 else f"{mn}-{mx}"
        sh = "lengths S/H/D/C " + "/".join(rng(sp.mins[i], sp.maxs[i]) for i in range(4))
    else:
        sh = sp.shape
    hon = []
    for suit, named, xc in sp.holdings:
        hon.append(f'{"".join(named)}{"x" * xc} of {suit}')
    for suit, n, m in sp.tops:
        hon.append(f"{n} of top {m} in {suit}")
    if sp.ctrl_lo > 0 or sp.ctrl_hi < 12:
        hon.append(f"{sp.ctrl_lo}-{sp.ctrl_hi} controls")
    honstr = ("; honours " + ", ".join(hon)) if hon else ""
    return f"{s}: {sp.lo}-{sp.hi} HCP, {sh}{honstr}"


def _contracts(*groups):
    return ", ".join(f"{c.label} {c.make_rate:.0f}% ({c.avg_score:+.0f})"
                     for c in (c for g in groups for c in g))


def _auc_strain(auc):
    return "".join(c for c in auc.contract if not c.isdigit())  # 'D' or 'NT'


def _resolve_contracts(result, q):
    """Contracts named in the question -> [(label, doubled_suffix, declarer)].

    Declarer comes from an explicit 'by <seat>', else the running auction when
    the strain matches, else None (best declarer)."""
    m = re.search(r"\bby\s+(north|south|east|west)\b", q, re.I)
    dec_hint = _SEATWORD[m.group(1).lower()] if m else None
    auc = getattr(result, "auction", None)
    refs, seen = [], set()

    def add(level, strain, x):
        strain = "NT" if strain in ("N", "NT") else strain
        dec = dec_hint
        if dec is None and auc is not None and _auc_strain(auc) == strain:
            dec = auc.declarer
        key = (level, strain, dec)
        if key not in seen:
            seen.add(key)
            refs.append((f"{level}{strain}", x.lower(), dec))

    for m in _CONTRACT_RE.finditer(q):
        add(m.group(1), m.group(2).upper(), m.group(3))
    for m in _SUIT_RE.finditer(q):
        w = m.group(2).lower().replace("-", "").replace(" ", "").rstrip("s")
        strain = "NT" if (w.startswith("notrump") or w == "nt") else _SUITMAP.get(w)
        if strain:
            add(m.group(1), strain, "")
    return refs


def _figures(result, q):
    """Injected make-rates for the contract(s) the question names, or ''."""
    lines = []
    for label, x, dec in _resolve_contracts(result, q):
        pct = result.contract_odds(label, dec)
        if pct is None:
            continue
        who = f" by {dec}" if dec else " (best declarer of the two)"
        dbl = " — doubling doesn't change the tricks, only the score" if x else ""
        lines.append(f"  {label}{x}{who}: makes {pct:.0f}% of deals{dbl}.")
    if not lines:
        return ""
    return ("Make-rates for the contract(s) in your question "
            "(from the DD trick distribution):\n" + "\n".join(lines))


def _summary(result):
    """Compact reference figures for an open-ended question."""
    us, them = result.side, result.opp_side
    bg, bs = result.best_game, result.best_slam
    bits = [f"{us} best game {bg.label} {bg.make_rate:.0f}%, "
            f"best slam {bs.label} {bs.make_rate:.0f}%."]
    if result.opp_best_game:
        og = result.opp_best_game
        bits.append(f"{them} best game {og.label} {og.make_rate:.0f}%.")
    if result.par:
        bits.append(f"Par to {us}: {result.par.avg_us:+.0f}.")
    d = decide(result)
    if d:
        bits.append(f"Standing decision: {d.question} (sim favours {d.recommend}).")
    return "For reference: " + " ".join(bits)


def _question_prompt(result, q):
    specs = result.config.specs if result.config else {}
    us = result.side
    L = [f"QUESTION (answer only this, nothing else): {q}", "",
         f"Double-dummy Monte-Carlo over {result.accepted} deals; our hands held "
         f"fixed, opponents dealt at random. We are {us}. Vulnerability "
         f"{result.vul} ({us} {'vul' if result.vul_us else 'not vul'})."]
    L.append("Hands:")
    L += ["  " + _seat(specs, s) for s in ORDER]
    a = getattr(result, "auction", None)
    if a:
        L.append(f"Auction: dealer {result.config.dealer}; final contract "
                 f"{a.contract}{'x' * a.doubled} by {a.declarer} ({a.side}).")
    fig = _figures(result, q)
    L.append(fig if fig else _summary(result))
    return "\n".join(L)


def _default_prompt(result):
    """The standard bid/stop (or competitive) verdict — no question asked."""
    specs = result.config.specs if result.config else {}
    us, them = result.side, result.opp_side
    bg, og = result.best_game, result.opp_best_game
    d = decide(result)

    L = [f"Double-dummy Monte-Carlo, {result.accepted} deals. We are {us}; "
         f"the opponents are {them}.",
         f"Vulnerability {result.vul}: {us} "
         f"{'vulnerable' if result.vul_us else 'not vulnerable'}, {them} "
         f"{'vulnerable' if result.vul_them else 'not vulnerable'}.",
         "Seats (our side first):"]
    L += ["  " + _seat(specs, s) for s in (us[0], us[1], them[0], them[1])]
    L.append(f"{us} (us) can make:   "
             f"{_contracts(result.games, result.slams, result.grands)}")
    if og:
        L.append(f"{them} (them) can make: {_contracts(result.opp_games, result.opp_slams)}")
        L.append(f"Best makeable: {us} {bg.label} {bg.make_rate:.0f}%; "
                 f"{them} {og.label} {og.make_rate:.0f}%.")
    a = result.auction
    if a and a.on_our_side:
        note = (" — WRONG SIDE: the long suit sits opposite the tenaces, so the "
                "auction cannot get the opening lead coming up to them" if a.wrong_side
                else "")
        L.append(f"Explicit auction fixes {a.declarer} as declarer of {a.contract}: it makes "
                 f"{a.dec_rate:.0f}% as actually played, versus {a.par_rate:.0f}% if {a.partner} "
                 f"could declare ({a.swing:+.0f}% swing){note}. Make-rates elsewhere use the "
                 f"realistic declarer (suit = long-trump hand).")
    if d:
        L.append(f"THE DECISION for {us}: {d.question}")
        L.append(f"  Options: {' | '.join(d.options)}. The simulation favours "
                 f"{d.recommend} (about {d.margin:+.0f} points/board).")
        if d.confidence:
            L.append(f"  Confidence {d.confidence}: {d.solidity:.0f}% of {d.contract}'s "
                     f"make-rate is position-proof (guaranteed regardless of where the "
                     f"defenders' cards lie); the rest hinges on card placement.")
        if d.evidence:
            L.append("  Drivers: " + "; ".join(f"{k} {v}" for k, v in d.evidence))
    p = result.par
    if p:
        tops = "; ".join(c.replace(",", " or ") for c, _ in p.top)
        L.append(f"Par ({us} view): average {p.avg_us:+.0f}; a doubled sacrifice is the "
                 f"par action on {p.sac_rate*100:.0f}% of boards; typical par: {tops}.")
    s = result.sacrifice
    if s and s.save_bid and result.zone == "competitive":
        from ..domain.contracts import is_game
        kind = "sacrifice over their game" if is_game(s.opp_game) else "partscore competition"
        L.append(f"Competitive decision ({kind}) for {us}: bid {s.save_bid} vs pass over "
                 f"{s.opp_game}. If {us} ALWAYS pass, average equity {s.avg_pass:+.0f}; "
                 f"if {us} ALWAYS bid {s.save_bid}, average {s.avg_bid:+.0f} (opponents "
                 f"double or bid on optimally), which beats passing on {s.bid_better*100:.0f}% "
                 f"of deals. So on average {'bidding' if s.recommend_bid else 'passing'} is better.")

    bd = result.breakdown
    if bd and bd.by_hcp:
        def slices(stats):
            return ", ".join(f"{s.label} {s.make_rate:.0f}%" for s in stats)
        L.append(f"{bd.contract_label} make-rate by {bd.focus_seat}'s hand:")
        L.append(f"  by HCP: {slices(bd.by_hcp)}")
        if bd.by_trump:
            L.append(f"  by trump length: {slices(bd.by_trump)}")
        if bd.by_short:
            L.append(f"  by shortest side-suit: {slices(bd.by_short)}")

    L.append(f"Reading the numbers: make% is how often the DECLARING side takes enough "
             f"tricks; the score in parentheses is that side's average points, so a "
             f"positive {them} score is a loss for {us}. Par is from {us}'s view "
             f"('x' = doubled).")
    L.append(f"Give the verdict: {d.question}" if d else "Give the verdict.")
    return "\n".join(L)


def build_prompt(result, question: str = "") -> str:
    """Serialize the result for Claude. A non-blank ``question`` switches to the
    focused question-first prompt; blank falls back to the standard verdict."""
    q = (question or "").strip()
    return _question_prompt(result, q) if q else _default_prompt(result)


def stream_explanation(prompt):
    """Yield text chunks of Claude's verdict. Requires ANTHROPIC_API_KEY."""
    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL, max_tokens=1024, system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        yield from stream.text_stream
