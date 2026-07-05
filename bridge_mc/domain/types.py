"""Typed data contracts shared across the whole app.

Replaces the old untyped result dict and positional seat-spec tuples. Derived
quantities (make-rate, 95% CI, "should we bid the slam") live here as
properties so every renderer computes them the same way, exactly once.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import opp_side, side_vul


@dataclass(frozen=True)
class SeatSpec:
    """How one seat (N/E/S/W) is dealt.

    kind is "random", "fixed", or "con" (constrained). Fields not relevant to a
    kind keep their defaults.
    """
    kind: str = "random"
    fixed: str = ""                       # "AK5 QJT 9432 K8" when kind == "fixed"
    lo: int = 0                           # HCP range when kind == "con"
    hi: int = 37
    shape: str = "any"                    # "any" | "bal" | "semibal" | "minlen"
    mins: tuple = (0, 0, 0, 0)            # S/H/D/C minimum lengths when shape == "minlen"
    maxs: tuple = (13, 13, 13, 13)        # S/H/D/C maximum lengths when shape == "minlen"
    holdings: tuple = ()                  # (suit, named_ranks, x_count) required holdings
    tops: tuple = ()                      # (suit, n, m): n of the top m in a suit
    ctrl_lo: int = 0                      # controls (A=2, K=1) range
    ctrl_hi: int = 12

    @staticmethod
    def random() -> "SeatSpec":
        return SeatSpec("random")

    @staticmethod
    def of_fixed(hand: str) -> "SeatSpec":
        return SeatSpec("fixed", fixed=hand)

    @staticmethod
    def constrained(lo: int, hi: int, shape: str, mins, maxs=None,
                    holdings=(), tops=(), ctrl=(0, 12)) -> "SeatSpec":
        return SeatSpec("con", lo=lo, hi=hi, shape=shape, mins=tuple(mins),
                        maxs=tuple(maxs) if maxs is not None else (13, 13, 13, 13),
                        holdings=tuple(holdings), tops=tuple(tops),
                        ctrl_lo=ctrl[0], ctrl_hi=ctrl[1])

    @property
    def has_honors(self) -> bool:
        return bool(self.holdings or self.tops or self.ctrl_lo > 0 or self.ctrl_hi < 12)

    @property
    def constrains(self) -> bool:
        """True if this seat imposes a filter beyond 'any 0-37'."""
        return self.kind == "con" and (
            self.lo > 0 or self.hi < 37 or self.shape != "any"
            or any(self.mins) or any(m < 13 for m in self.maxs) or self.has_honors)


@dataclass(frozen=True)
class SimConfig:
    specs: dict            # seat -> SeatSpec
    n: int                 # target accepted deals
    max_tries: int
    seed: str = ""
    side: str = "NS"       # protagonist side ("us"); the other side is "them"
    vul: str = "None"      # board vulnerability: None / NS / EW / Both
    n_samples: int = 6


@dataclass(frozen=True)
class SampleDeal:
    hands: dict            # seat -> "♠AK5 ♥QJT ♦9432 ♣K8"
    tricks: dict           # strain -> DD tricks for the analysed side
    par: str = ""          # par contract(s) on this deal, e.g. "EW 5Dx"
    par_score: int = 0     # par score from our side's perspective


@dataclass(frozen=True)
class ContractStat:
    label: str
    makes: int
    trials: int
    avg_score: float | None = None        # None for summary rows (any game / grand)

    @property
    def make_rate(self) -> float:
        return 100 * self.makes / self.trials if self.trials else 0.0

    @property
    def ci95(self) -> float:
        p = self.make_rate
        return 1.96 * (p * (100 - p) / self.trials) ** 0.5 if self.trials else 0.0


@dataclass(frozen=True)
class Par:
    """Optimal competitive result (DDS par) aggregated over the run.

    ``avg_us`` is the average par score from the protagonist side's view — the
    expected outcome of double-dummy-optimal competitive bidding, already
    accounting for doubled sacrifices and both vulnerabilities. ``sac_rate`` is
    the fraction of boards whose par contract is a doubled sacrifice.
    """
    avg_us: float
    sac_rate: float
    top: list = field(default_factory=list)     # list[(contract_str, count)]


@dataclass(frozen=True)
class Sacrifice:
    """'Bid the save vs pass' equity for our side on a competitive deal."""
    opp_game: str          # the opponents' game we're defending against, e.g. "4S"
    save_bid: str          # our typical save, e.g. "5D"
    avg_pass: float        # avg equity if we always pass
    avg_bid: float         # avg equity if we always bid the save (they respond best)
    bid_better: float      # fraction of deals where bidding beats passing

    @property
    def recommend_bid(self) -> bool:
        return self.avg_bid > self.avg_pass


@dataclass(frozen=True)
class Breakdown:
    """How the *decision* contract's make-rate depends on the constrained seat.

    The decision contract is whatever you're weighing whether to bid — the best
    slam when a slam is live, otherwise the best game. Answers 'which partner
    hands should bid on', sliced by HCP, by trump-support length, and by
    short-suit (ruffing) value. Each slice is a ContractStat (make_rate + 95% CI).
    """
    focus_seat: str                 # e.g. "N" — the constrained seat analysed
    contract_label: str             # the decision contract, e.g. "6D" or "4H"
    trump_suit: str | None          # "D"/"H"/... or None for a NT contract
    by_hcp: list = field(default_factory=list)     # list[ContractStat]
    by_trump: list = field(default_factory=list)
    by_short: list = field(default_factory=list)


@dataclass(frozen=True)
class SimResult:
    config: SimConfig
    accepted: int
    tries: int
    games: list = field(default_factory=list)     # list[ContractStat]
    slams: list = field(default_factory=list)
    grands: list = field(default_factory=list)
    any_game: ContractStat | None = None
    any_slam: ContractStat | None = None
    grand: ContractStat | None = None
    best_game: ContractStat | None = None
    best_slam: ContractStat | None = None
    best_grand: ContractStat | None = None
    ev_diff: float = 0.0
    imp: float | None = None
    samples: list = field(default_factory=list)    # list[SampleDeal]
    breakdown: "Breakdown | None" = None
    # The opposing side ("them") — for competitive-auction judgement.
    opp_games: list = field(default_factory=list)  # list[ContractStat]
    opp_slams: list = field(default_factory=list)
    opp_best_game: ContractStat | None = None
    opp_best_slam: ContractStat | None = None
    par: "Par | None" = None
    zone: str = "game"     # "slam" | "game" | "competitive" — which analysis fits
    sacrifice: "Sacrifice | None" = None

    @property
    def empty(self) -> bool:
        return self.accepted == 0

    @property
    def side(self) -> str:
        return self.config.side

    @property
    def opp_side(self) -> str:
        return opp_side(self.config.side)

    @property
    def vul(self) -> str:
        return self.config.vul

    @property
    def vul_us(self) -> bool:
        return side_vul(self.config.vul, self.config.side)

    @property
    def vul_them(self) -> bool:
        return side_vul(self.config.vul, opp_side(self.config.side))

    @property
    def accept_rate(self) -> float:
        return 100 * self.accepted / self.tries if self.tries else 0.0

    @property
    def bid_slam(self) -> bool:
        return self.ev_diff > 0

    @property
    def bid_grand(self) -> bool:
        """The grand is the highest-EV contract — worth more than the small slam."""
        g, s, gm = self.best_grand, self.best_slam, self.best_game
        return (g is not None and s is not None and gm is not None
                and g.avg_score > s.avg_score and g.avg_score > gm.avg_score)

    def by_label(self, label: str) -> ContractStat | None:
        for s in (*self.games, *self.slams):
            if s.label == label:
                return s
        return None
