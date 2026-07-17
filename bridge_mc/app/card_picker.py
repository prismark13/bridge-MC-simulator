"""A modal card picker for building a fixed hand without duplicating cards.

Cards already assigned to another seat are shown disabled, so the same card
can never be dealt twice. OK is enabled only on a legal 13-card hand.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout)

from ..domain.contracts import SUIT_SYM, SUITS

RANKS = list("AKQJT98765432")
_HCP = {"A": 4, "K": 3, "Q": 2, "J": 1}
_RED = "#b0243a"
_SEL = "#2c7a50"        # green highlight for a selected card
_ORDER = {r: i for i, r in enumerate(RANKS)}


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

        # Live preview of the hand as it's built up, card by card.
        self.preview = QLabel()
        self.preview.setTextFormat(Qt.TextFormat.RichText)
        self.preview.setStyleSheet(
            "font-family:Consolas,monospace;font-size:18px;padding:6px 2px;")
        root.addWidget(self.preview)

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
                base = f"color:{_RED};" if suit in ("H", "D") else ""
                b.setStyleSheet(
                    f"QPushButton{{{base}font-weight:600}}"
                    f"QPushButton:checked{{background:{_SEL};color:#fff;"
                    f"border:1px solid {_SEL};font-weight:700}}"
                    "QPushButton:disabled{color:#888}")
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
            self.buttons[card].setChecked(False)
        elif len(self.selected) < 13:
            self.selected.add(card)
            self.buttons[card].setChecked(True)
        else:
            self.buttons[card].setChecked(False)   # at 13 already — revert
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
        self.preview.setText(self._preview_html())

    def _preview_html(self):
        cells = []
        for s in SUITS:
            rs = sorted((r for (su, r) in self.selected if su == s),
                        key=lambda r: _ORDER[r])
            txt = "".join("10" if r == "T" else r for r in rs) or "—"
            col = f' style="color:{_RED}"' if s in ("H", "D") else ""
            cells.append(f'<span{col}>{SUIT_SYM[s]}&nbsp;{txt}</span>')
        return "&nbsp;&nbsp;&nbsp;".join(cells)

    def hand_string(self):
        parts = []
        for s in SUITS:
            rs = sorted((r for (su, r) in self.selected if su == s),
                        key=lambda r: _ORDER[r])
            parts.append("".join(rs) or "-")
        return " ".join(parts)


def _named(s):
    """Ranks named in a holding string (ignores x); '10' -> 'T'."""
    s = (s or "").upper().replace("10", "T")
    return [ch for ch in s if ch in _ORDER]


class SuitPicker(QDialog):
    """Pick one suit combination: a row of cards per hand. Click a card in the
    Hand 1 row to give it to Hand 1, in the Hand 2 row to give it to Hand 2.
    Click it again to hand it back to the defenders. A card can only be in one
    place, so clicking it in one row takes it out of the other."""
    _H1 = "#3a68b0"         # blue
    _H2 = "#2c7a50"         # green

    def __init__(self, parent, top="", bottom=""):
        super().__init__(parent)
        self.setWindowTitle("Pick the suit")
        self.state = {r: None for r in RANKS}      # None | "1" | "2"
        for r in _named(top):
            self.state[r] = "1"
        for r in _named(bottom):
            self.state[r] = "2"
        self.buttons = {"1": {}, "2": {}}

        root = QVBoxLayout(self)
        info = QLabel("Click a card in a row to give it to that hand; click again "
                      "to give it back to the defenders.")
        info.setStyleSheet("color:#888;")
        root.addWidget(info)

        self.preview = QLabel()
        self.preview.setTextFormat(Qt.TextFormat.RichText)
        self.preview.setStyleSheet(
            "font-family:Consolas,monospace;font-size:16px;padding:8px 2px;")
        root.addWidget(self.preview)

        grid = QGridLayout()
        grid.setHorizontalSpacing(3)
        grid.setVerticalSpacing(6)
        for row, (hand, colour) in enumerate(((("1"), self._H1), (("2"), self._H2))):
            lab = QLabel(f"Hand {hand}")
            lab.setStyleSheet(f"color:{colour};font-weight:700;padding-right:8px")
            grid.addWidget(lab, row, 0)
            for ci, rank in enumerate(RANKS):
                b = QPushButton("10" if rank == "T" else rank)
                b.setFixedSize(38, 32)
                b.clicked.connect(
                    lambda _=False, rr=rank, hh=hand: self._toggle(rr, hh))
                grid.addWidget(b, row, ci + 1)
                self.buttons[hand][rank] = b
        root.addLayout(grid)

        foot = QHBoxLayout()
        self.count = QLabel()
        self.count.setStyleSheet("color:#888")
        foot.addWidget(self.count)
        foot.addStretch(1)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        foot.addWidget(clear)
        root.addLayout(foot)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        root.addWidget(box)
        self._refresh()

    def _toggle(self, rank, hand):
        self.state[rank] = None if self.state[rank] == hand else hand
        self._refresh()

    def _clear(self):
        self.state = {r: None for r in RANKS}
        self._refresh()

    def _refresh(self):
        for hand, colour in (("1", self._H1), ("2", self._H2)):
            for rank, b in self.buttons[hand].items():
                if self.state[rank] == hand:
                    b.setStyleSheet(f"QPushButton{{background:{colour};color:#fff;"
                                    f"border:1px solid {colour};font-weight:700}}")
                elif self.state[rank] is not None:
                    b.setStyleSheet("QPushButton{color:#666}")   # in the other hand
                else:
                    b.setStyleSheet("QPushButton{font-weight:600}")
        h1, h2 = self.holdings()
        opps = "".join(r for r in RANKS if self.state[r] is None) or "—"
        self.preview.setText(
            f'<span style="color:{self._H1}">Hand 1&nbsp; <b>{h1 or "void"}</b></span>'
            f'&nbsp;&nbsp;&nbsp;<span style="color:{self._H2}">Hand 2&nbsp; <b>{h2 or "void"}</b></span>'
            f'&nbsp;&nbsp;&nbsp;<span style="color:#888">Defenders&nbsp; {opps}</span>')
        self.count.setText(f"Hand 1: {len(h1)}   Hand 2: {len(h2)}   "
                           f"defenders: {13 - len(h1) - len(h2)}")

    def holdings(self):
        h1 = "".join(r for r in RANKS if self.state[r] == "1")
        h2 = "".join(r for r in RANKS if self.state[r] == "2")
        return h1, h2
