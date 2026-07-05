"""Claude 'Explain' verdict.

`build_prompt` is pure (SimResult -> str). `stream_explanation` is the only
place that touches the anthropic SDK, yielding text chunks so any front-end can
adapt the stream to its own event model.
"""
from ..domain.contracts import ORDER

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
    "the one or two factors that most drive the outcome. When the results "
    "include a make-rate breakdown by the constrained hand's HCP / trump length "
    "/ shortness, use it to say WHICH hands should bid on. Keep it practical, "
    "roughly 120-200 words, and use ONLY the numbers provided; do not invent new "
    "ones. Plain text, no preamble."
)


def build_prompt(result, question: str = "") -> str:
    """Serialize the result for Claude. ``question`` steers the analysis;
    blank falls back to the standard bid/stop verdict."""
    specs = result.config.specs if result.config else {}

    def seat(s):
        sp = specs.get(s)
        if sp is None or sp.kind == "random":
            return f"{s}: random"
        if sp.kind == "fixed":
            return f"{s}: {sp.fixed}"
        sh = sp.shape if sp.shape != "minlen" else "min " + "/".join(map(str, sp.mins))
        return f"{s}: {sp.lo}-{sp.hi} HCP, {sh}"

    n = result.accepted
    L = [f"Double-dummy Monte-Carlo, {n} deals. Analysing {result.side}, "
         f"{'vulnerable' if result.vul else 'non-vul'}.", "Hands:"]
    L += ["  " + seat(s) for s in ORDER]
    L.append("Contract  make%  avgScore:")
    for stat in (*result.games, *result.slams):
        L.append(f"  {stat.label:<4} {stat.make_rate:4.0f}%  {stat.avg_score:+5.0f}")
    L.append(f"  any game {result.any_game.make_rate:.0f}%, "
             f"any slam {result.any_slam.make_rate:.0f}%, "
             f"grand {result.grand.make_rate:.0f}%")
    bg, bs = result.best_game, result.best_slam
    imp = f", {result.imp:+.2f} IMP/board" if result.imp is not None else ""
    L.append(f"Best game {bg.label} EV {bg.avg_score:+.0f}; "
             f"best slam {bs.label} EV {bs.avg_score:+.0f}; "
             f"slam vs game {result.ev_diff:+.0f} pts{imp}.")

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

    q = (question or "").strip()
    L.append(f"User question: {q}" if q else "Give the verdict.")
    return "\n".join(L)


def stream_explanation(prompt):
    """Yield text chunks of Claude's verdict. Requires ANTHROPIC_API_KEY."""
    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL, max_tokens=1024, system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        yield from stream.text_stream
