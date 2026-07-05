"""Typed data contracts shared across the whole app.

Replaces the old untyped result dict and positional seat-spec tuples. Derived
quantities (make-rate, 95% CI, "should we bid the slam") live here as
properties so every renderer computes them the same way, exactly once.
"""
from __future__ import annotations

from dataclasses import dataclass, field


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

    @staticmethod
    def random() -> "SeatSpec":
        return SeatSpec("random")

    @staticmethod
    def of_fixed(hand: str) -> "SeatSpec":
        return SeatSpec("fixed", fixed=hand)

    @staticmethod
    def constrained(lo: int, hi: int, shape: str, mins) -> "SeatSpec":
        return SeatSpec("con", lo=lo, hi=hi, shape=shape, mins=tuple(mins))

    @property
    def constrains(self) -> bool:
        """True if this seat imposes a filter beyond 'any 0-37'."""
        return self.kind == "con" and (
            self.lo > 0 or self.hi < 37 or self.shape != "any" or any(self.mins))


@dataclass(frozen=True)
class SimConfig:
    specs: dict            # seat -> SeatSpec
    n: int                 # target accepted deals
    max_tries: int
    seed: str = ""
    side: str = "NS"       # side being analysed
    vul: bool = False
    n_samples: int = 6


@dataclass(frozen=True)
class SampleDeal:
    hands: dict            # seat -> "♠AK5 ♥QJT ♦9432 ♣K8"
    tricks: dict           # strain -> DD tricks for the analysed side


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
    any_game: ContractStat | None = None
    any_slam: ContractStat | None = None
    grand: ContractStat | None = None
    best_game: ContractStat | None = None
    best_slam: ContractStat | None = None
    ev_diff: float = 0.0
    imp: float | None = None
    samples: list = field(default_factory=list)    # list[SampleDeal]
    breakdown: "Breakdown | None" = None

    @property
    def empty(self) -> bool:
        return self.accepted == 0

    @property
    def side(self) -> str:
        return self.config.side

    @property
    def vul(self) -> bool:
        return self.config.vul

    @property
    def accept_rate(self) -> float:
        return 100 * self.accepted / self.tries if self.tries else 0.0

    @property
    def bid_slam(self) -> bool:
        return self.ev_diff > 0

    def by_label(self, label: str) -> ContractStat | None:
        for s in (*self.games, *self.slams):
            if s.label == label:
                return s
        return None
