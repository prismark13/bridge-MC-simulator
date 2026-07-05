"""A modal card picker for building a fixed hand without duplicating cards.

Cards already assigned to another seat are shown disabled, so the same card
can never be dealt twice. OK is enabled only on a legal 13-card hand.
"""
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout)

from ..domain.contracts import SUIT_SYM, SUITS

RANKS = list("AKQJT98765432")
_HCP = {"A": 4, "K": 3, "Q": 2, "J": 1}
_RED = "#b0243a"


class CardPicker(QDialog):
    def __init__(self, parent, seat, current, used):
        """current/used are sets/dicts of (suit, rank). ``used`` maps a card to
        the seat already holding it (those cards are disabled)."""
        super().__init__(parent)
        self.setWindowTitle(f"Pick {seat}'s hand")
        self.selected = set(current)
        self.used = dict(used)
        self.buttons = {}

        root = QVBoxLayout(self)
        info = QLabel("Click 13 cards. Greyed cards are already in another hand.")
        info.setStyleSheet("color:#888;")
        root.addWidget(info)

        grid = QGridLayout()
        grid.setHorizontalSpacing(3)
        grid.setVerticalSpacing(3)
        for ri, suit in enumerate(SUITS):
            pip = QLabel(SUIT_SYM[suit])
            pip.setStyleSheet(f"font-size:16px;font-weight:600;"
                              + (f"color:{_RED};" if suit in ("H", "D") else ""))
            grid.addWidget(pip, ri, 0)
            for ci, rank in enumerate(RANKS):
                card = (suit, rank)
                b = QPushButton("10" if rank == "T" else rank)
                b.setCheckable(True)
                b.setFixedSize(36, 30)
                if suit in ("H", "D"):
                    b.setStyleSheet(f"color:{_RED};")
                if card in self.used:
                    b.setEnabled(False)
                    b.setToolTip(f"already in {self.used[card]}'s hand")
                elif card in self.selected:
                    b.setChecked(True)
                b.clicked.connect(lambda _=False, c=card: self._toggle(c))
                grid.addWidget(b, ri, ci + 1)
                self.buttons[card] = b
        root.addLayout(grid)

        foot = QHBoxLayout()
        self.count = QLabel()
        foot.addWidget(self.count)
        foot.addStretch(1)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        foot.addWidget(clear)
        root.addLayout(foot)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = box.button(QDialogButtonBox.StandardButton.Ok)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)

        self._refresh()

    def _toggle(self, card):
        if card in self.selected:
            self.selected.discard(card)
        elif len(self.selected) < 13:
            self.selected.add(card)
        else:
            self.buttons[card].setChecked(False)   # at 13 already
        self._refresh()

    def _clear(self):
        self.selected.clear()
        for card, b in self.buttons.items():
            if b.isEnabled():
                b.setChecked(False)
        self._refresh()

    def _refresh(self):
        n = len(self.selected)
        hcp = sum(_HCP.get(r, 0) for _, r in self.selected)
        counts = {s: sum(1 for (su, _) in self.selected if su == s) for s in SUITS}
        shape = "-".join(str(counts[s]) for s in SUITS)
        self.count.setText(f"{n}/13 cards   {hcp} HCP   shape {shape}")
        self.ok.setEnabled(n == 13)

    def hand_string(self):
        order = {r: i for i, r in enumerate(RANKS)}
        parts = []
        for s in SUITS:
            rs = sorted((r for (su, r) in self.selected if su == s),
                        key=lambda r: order[r])
            parts.append("".join(rs) or "-")
        return " ".join(parts)
