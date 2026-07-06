"""The single bidding decision a deal set poses.

Consolidates the scattered zone / bid_slam / bid_grand / sacrifice flags into
one first-class ``Decision`` that the report and the Claude prompt both key off,
so they always speak the same question. Pure — derived from a SimResult.

The decisions sit on two axes:
  constructive ladder :  GAME  ->  SLAM  ->  GRAND        (how high to bid our hand)
  competitive         :  COMPETE (overcall/partscore)  ->  SACRIFICE (save over a game)

COMPETE covers the *overcall / partscore-competition* entry decision (bid vs
pass over a low opponent contract). Pure "should I open / respond" is a
hand-evaluation question (Rule of 20, controls), not a contract make-rate, so it
is out of scope here — the simulation answers what a partnership can make, not
whether a single hand is worth an opening bid.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import is_game, opp_side


@dataclass(frozen=True)
class Decision:
    kind: str            # GAME | SLAM | GRAND | COMPETE | SACRIFICE
    axis: str            # "constructive" | "competitive"
    verb: str            # hero lead-in, e.g. "Bid the grand &mdash;"
    contract: str        # focal contract label the hero names, e.g. "7H"
    headline: float      # the % to show large in the hero
    tone: str            # "good" | "warn"
    detail: str          # one-sentence prose (contract labels get suit-symbolised)
    question: str        # the decision, phrased for Claude / the Ask box
    options: list = field(default_factory=list)     # the choices, e.g. ["6H (small slam)", "7H (grand)"]
    recommend: str = ""  # the recommended option
    margin: float = 0.0  # expected points/board the recommendation gains
    evidence: list = field(default_factory=list)    # [(label, value)] numbers this decision turns on
    confidence: str = "" # High/Medium/Low — how position-proof the recommendation is (finesse mode)
    solidity: float = 0.0  # % of the make-rate that is position-proof (guaranteed)


def decide(result) -> "Decision | None":
    """Classify what this deal set is really asking."""
    if result.empty:
        return None
    us, opp = result.side, result.opp_side
    bg, bs, gr = result.best_game, result.best_slam, result.best_grand
    og = result.opp_best_game
    s = result.sacrifice

    # ---- competitive axis: we can't make game, it's about their hand ----
    if result.zone == "competitive" and og is not None:
        save = s.save_bid if s and s.save_bid else ""
        opp_ct = s.opp_game if s and s.opp_game else og.label
        opp_is_game = is_game(opp_ct)
        rec_bid = bool(s and s.recommend_bid)
        kind = "SACRIFICE" if opp_is_game else "COMPETE"
        act = "sacrifice in" if opp_is_game else "compete/overcall"
        parbit = ""
        if result.par:
            parbit = (f" Par is {result.par.avg_us:+.0f} to you; a save is par on "
                      f"{result.par.sac_rate * 100:.0f}% of boards.")
        detail = (f"Your best is only {bg.label} at {bg.make_rate:.0f}% — this is a "
                  f"competitive decision, not a constructive one.{parbit}")
        question = (f"Should {us} {act} {save} over {opp}'s {opp_ct}, or pass"
                    f"{' and defend' if opp_is_game else ''}?")
        options = [f"Pass (let {opp} play {opp_ct})",
                   f"Bid {save}" + (" (sacrifice)" if opp_is_game else " (compete)")]
        evidence = [
            (f"{us} best game", f"{bg.label} {bg.make_rate:.0f}%"),
            (f"{opp} owns", f"{opp_ct} {og.make_rate:.0f}%"),
        ]
        if result.par:
            evidence.append(("par (you)", f"{result.par.avg_us:+.0f}"))
        if s and save:
            evidence.append((f"{save} vs pass",
                             f"pass {s.avg_pass:+.0f} / bid {s.avg_bid:+.0f}, "
                             f"bid better {s.bid_better * 100:.0f}%"))
        verb = (f"They own this hand &mdash; {opp} make" if opp_is_game
                else f"Partscore battle &mdash; {opp} in")
        return Decision(
            kind=kind, axis="competitive",
            verb=verb, contract=opp_ct,
            headline=og.make_rate, tone="warn", detail=detail, question=question,
            options=options, recommend=(f"Bid {save}" if rec_bid else "Pass"),
            margin=(abs(s.avg_bid - s.avg_pass) if s else 0.0), evidence=evidence)

    # ---- constructive ladder ----
    ev = [("best game", f"{bg.label} {bg.make_rate:.0f}% (EV {bg.avg_score:+.0f})"),
          ("small slam", f"{bs.label} {bs.make_rate:.0f}% (EV {bs.avg_score:+.0f})"),
          ("grand", f"{gr.label} {gr.make_rate:.0f}% (EV {gr.avg_score:+.0f})")]

    def floor(c):
        """Single-dummy floor clause for the focal contract (finesse mode only)."""
        if not result.finesse:
            return ""
        return (f" Single-dummy floor (position-proof, if the finesses lie wrong): "
                f"{c.proof_rate:.0f}%.")

    def conf(c):
        """Confidence in the recommendation from how position-proof it is."""
        if not result.finesse or c.make_rate < 1:
            return "", 0.0
        sol = 100 * c.proof_rate / c.make_rate
        lvl = "High" if sol >= 85 else "Medium" if sol >= 60 else "Low"
        return lvl, sol

    if result.bid_grand:
        return Decision(
            kind="GRAND", axis="constructive", verb="Bid the grand &mdash;",
            contract=gr.label, headline=gr.make_rate, tone="good",
            detail=(f"7-level {gr.label} makes {gr.make_rate:.0f}% — nearly as often as the "
                    f"small slam {bs.label} ({bs.make_rate:.0f}%) — so it beats it by "
                    f"{gr.avg_score - bs.avg_score:+.0f} pts. Don't stop in six." + floor(gr)),
            question=f"Should {us} bid the grand slam {gr.label}, or stop in the small slam {bs.label}?",
            options=[f"{bs.label} (small slam)", f"{gr.label} (grand)"],
            recommend=gr.label, margin=gr.avg_score - bs.avg_score, evidence=ev,
            confidence=conf(gr)[0], solidity=conf(gr)[1])

    if result.bid_slam:
        imp = f" · {result.imp:+.2f} IMP/board" if result.imp is not None else ""
        return Decision(
            kind="SLAM", axis="constructive", verb="Bid the slam &mdash;",
            contract=bs.label, headline=bs.make_rate, tone="good",
            detail=(f"6-level {bs.label} makes this often and beats the best game "
                    f"by {result.ev_diff:+.0f} pts{imp}." + floor(bs)),
            question=f"Should {us} bid the small slam {bs.label}, or stop in game {bg.label}?",
            options=[f"{bg.label} (game)", f"{bs.label} (small slam)"],
            recommend=bs.label, margin=result.ev_diff, evidence=ev,
            confidence=conf(bs)[0], solidity=conf(bs)[1])

    return Decision(
        kind="GAME", axis="constructive", verb="Game is the limit &mdash;",
        contract=bg.label, headline=bg.make_rate, tone="warn",
        detail=(f"The best game {bg.label} is the ceiling — the slam "
                f"loses {result.ev_diff:+.0f} pts to it." + floor(bg)),
        question=f"Should {us} try for slam {bs.label}, or is game {bg.label} the limit?",
        options=[f"Stop in {bg.label}", f"Try for slam {bs.label}"],
        recommend=f"Stop in {bg.label}", margin=-result.ev_diff, evidence=ev,
        confidence=conf(bg)[0], solidity=conf(bg)[1])
