"""Pure domain layer: data shapes, contract constants, input parsing.

Nothing here imports redeal, Qt, anthropic, or performs I/O. This is the layer
that unit tests exercise without a display or the DDS DLL.
"""
from .auction import declarer_from_auction, parse_bid
from .contracts import (
    ALL_CS, ATTR, GAMES, ORDER, RANKS, SIDE_IDX, SLAMS, STRAINS, SUITS,
    SUIT_SYM, VUL_LABEL, VUL_STATES, is_game, opp_side, side_vul, to_imps,
)
from .decision import Decision, decide
from .parsing import (
    build_specs, parse_fixed, parse_honors, parse_shape, parse_suit)
from .types import (
    AuctionResult, Breakdown, ContractStat, Par, Sacrifice, SampleDeal,
    SeatSpec, SimConfig, SimResult,
)

__all__ = [
    "ALL_CS", "ATTR", "GAMES", "ORDER", "RANKS", "SIDE_IDX", "SLAMS",
    "STRAINS", "SUITS", "SUIT_SYM", "VUL_LABEL", "VUL_STATES",
    "is_game", "opp_side", "side_vul", "to_imps", "Decision", "decide",
    "declarer_from_auction", "parse_bid",
    "build_specs", "parse_fixed", "parse_honors", "parse_shape", "parse_suit",
    "AuctionResult", "Breakdown", "ContractStat", "Par", "Sacrifice",
    "SampleDeal", "SeatSpec", "SimConfig", "SimResult",
]
