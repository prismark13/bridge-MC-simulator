"""Claude 'Explain' verdict.

`build_prompt` is pure (SimResult -> str). `stream_explanation` is the only
place that touches the anthropic SDK, yielding text chunks so any front-end can
adapt the stream to its own event model.
"""
try:
    import anthropic
    HAVE_ANTHROPIC = True
except Exception:
    HAVE_ANTHROPIC = False

MODEL = "claude-opus-4-8"
SYSTEM = (
    "You are an expert bridge analyst. You are given the results of a "
    "double-dummy Monte-Carlo simulation for one deal setup. If the user asks a "
    "specific question, answer that directly; otherwise give a concise, "
    "practical verdict: whether to bid on or stop, the safety-net contract, and "
    "the one or two factors that most drive the outcome. Both sides' makeable "
    "contracts are given with the board vulnerability, so when relevant judge "
    "the competitive decision (compete, double, or sacrifice) — a sacrifice is "
    "good when its expected cost is less than the opponents' making contract. "
    "When the results include a make-rate breakdown by the constrained hand's "
    "HCP / trump length / shortness, use it to say WHICH hands should bid on. "
    "Keep it practical, "
    "roughly 120-200 words, and use ONLY the numbers provided; do not invent new "
    "ones. Plain text, no preamble."
)


def build_prompt(result, question: str = "") -> str:
    """Serialize the result for Claude. ``question`` steers the analysis;
    blank falls back to the standard bid/stop verdict."""
    specs = result.config.specs if result.config else {}
    us, them = result.side, result.opp_side

    def seat(s):
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
            sh = "lengths S/H/D/C " + "/".join(
                rng(sp.mins[i], sp.maxs[i]) for i in range(4))
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

    def contracts(*groups):
        return ", ".join(f"{c.label} {c.make_rate:.0f}% ({c.avg_score:+.0f})"
                         for c in (c for g in groups for c in g))

    bg = result.best_game
    og = result.opp_best_game

    L = [f"Double-dummy Monte-Carlo, {result.accepted} deals. We are {us}; "
         f"the opponents are {them}.",
         f"Vulnerability {result.vul}: {us} "
         f"{'vulnerable' if result.vul_us else 'not vulnerable'}, {them} "
         f"{'vulnerable' if result.vul_them else 'not vulnerable'}.",
         "Seats (our side first):"]
    L += ["  " + seat(s) for s in (us[0], us[1], them[0], them[1])]
    L.append(f"{us} (us) can make:   "
             f"{contracts(result.games, result.slams, result.grands)}")
    if og:
        L.append(f"{them} (them) can make: {contracts(result.opp_games, result.opp_slams)}")
        L.append(f"Best makeable: {us} {bg.label} {bg.make_rate:.0f}%; "
                 f"{them} {og.label} {og.make_rate:.0f}%.")
    if result.bid_grand:
        gr, bs = result.best_grand, result.best_slam
        L.append(f"NOTE: the grand slam {gr.label} makes {gr.make_rate:.0f}%, nearly as often "
                 f"as the small slam {bs.label} ({bs.make_rate:.0f}%), so bidding {gr.label} is "
                 f"worth {gr.avg_score - bs.avg_score:+.0f} points over stopping in six — "
                 f"recommend the grand.")
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

    if result.finesse:
        c = result.best_slam if result.bid_slam else result.best_game
        if c:
            L.append(f"Card placement (deals re-solved with E/W swapped): {c.label} is "
                     f"{c.proof_rate:.0f}% position-proof (makes however the defenders' cards lie) "
                     f"and {c.sens_rate:.0f}% hinges on reading the cards; a pure two-way guess "
                     f"reads as position-proof, so position-proof is a floor.")
    L.append(f"Reading the numbers: make% is how often the DECLARING side takes enough "
             f"tricks; the score in parentheses is that side's average points, so a "
             f"positive {them} score is a loss for {us}. Par is from {us}'s view "
             f"('x' = doubled).")
    q = (question or "").strip()
    L.append(f"Question: {q}" if q else "Give the bid-or-stop verdict for us.")
    return "\n".join(L)


def stream_explanation(prompt):
    """Yield text chunks of Claude's verdict. Requires ANTHROPIC_API_KEY."""
    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL, max_tokens=1024, system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        yield from stream.text_stream
