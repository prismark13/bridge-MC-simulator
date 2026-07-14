"""PySide6 main window. Thin adapter: collect inputs, drive the engine in a
worker thread, render the SimResult to the embedded Chromium view + Log tab.
"""
import os
import sys
import tempfile
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit,
    QPushButton, QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..ai import HAVE_ANTHROPIC, build_prompt
from ..domain import (
    ORDER, SUITS, VUL_LABEL, VUL_STATES, SimConfig, build_specs, parse_suit)
from ..engine.sampling import smart_seat
from ..report import render_html, render_text
from .card_picker import CardPicker, SuitPicker
from .theming import apply_palette
from .workers import AiWorker, SimWorker, SuitWorker

DEFAULTS = {
    "N": ("Constrain", "", 6, 10, "3 0 0 0"),
    "E": ("Random", "", 0, 37, "any"),
    "S": ("Fixed", "AKQ76 AK5 A42 32", 0, 37, "any"),
    "W": ("Random", "", 0, 37, "any"),
}
DEFAULT_SIDE = "NS"
DEFAULT_VUL = "None"
DEFAULT_DEALS = 2000
DEFAULT_DEALER = "N"
DEFAULT_AUCTION = ""
DEFAULT_ASK = ""
MODES = ["Random", "Fixed", "Constrain"]
# Report sections shown by default (the rest are one click away).
REPORT_DEFAULT = {"tiles", "auction", "hands", "competitive"}
SUIT_SYM = {"C": "♣", "D": "♦", "H": "♥", "S": "♠"}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bridge MC Simulator")
        self.resize(1040, 820)
        self.setMinimumSize(720, 640)
        self.theme = "light"
        self.sim = None
        self.ai = None
        self.last_result = None
        self.last_html = None
        self._answer = ""
        self._question = ""
        self.mode, self.hand, self.hlo, self.hhi, self.shp = {}, {}, {}, {}, {}
        self.hon = {}
        self._build()
        apply_palette(QApplication.instance(), "light")

    # ---------------------------------------------------------------- layout
    def _build(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14); root.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("Bridge MC Simulator")
        title.setStyleSheet("font-size:17px;font-weight:600;")
        head.addWidget(title); head.addStretch(1)
        self.theme_btn = QPushButton("◐ Theme")
        self.theme_btn.clicked.connect(self._toggle_theme)
        head.addWidget(self.theme_btn)
        root.addLayout(head)

        hands = QGroupBox("Hands")
        g = QGridLayout(hands)
        g.setContentsMargins(10, 8, 10, 10)
        g.setHorizontalSpacing(6); g.setVerticalSpacing(4)
        hint = QLabel("Fixed: click 'Cards…' or type '♠ ♥ ♦ ♣' e.g. AK5 QJT 9432 K8      "
                      "Shape: bal / semibal / any / lengths '0 5 4 0' or '3-5 5+ 0-4 x'")
        hint.setStyleSheet("color:#888;")
        g.addWidget(hint, 0, 0, 1, 9)
        for c, h in enumerate(["", "Mode", "Fixed hand", "HCP", "", "", "Shape", "Honors"]):
            lbl = QLabel(h); lbl.setStyleSheet("font-weight:600;")
            g.addWidget(lbl, 1, c)
        for i, seat in enumerate(ORDER, start=2):
            m, hnd, lo, hi, shp = DEFAULTS[seat]
            slbl = QLabel(seat); slbl.setStyleSheet("font-weight:600;font-size:13px;")
            g.addWidget(slbl, i, 0)
            cb = QComboBox(); cb.addItems(MODES); cb.setCurrentText(m)
            cb.currentTextChanged.connect(lambda _t, s=seat: self._sync(s))
            g.addWidget(cb, i, 1); self.mode[seat] = cb
            e = QLineEdit(hnd); e.setMinimumWidth(180)
            g.addWidget(e, i, 2); self.hand[seat] = e
            slo = QSpinBox(); slo.setRange(0, 37); slo.setValue(lo)
            g.addWidget(slo, i, 3); self.hlo[seat] = slo
            g.addWidget(QLabel("–"), i, 4)
            shi = QSpinBox(); shi.setRange(0, 37); shi.setValue(hi)
            g.addWidget(shi, i, 5); self.hhi[seat] = shi
            es = QLineEdit(shp); es.setMaximumWidth(110)
            g.addWidget(es, i, 6); self.shp[seat] = es
            eh = QLineEdit(); eh.setMaximumWidth(120)
            eh.setPlaceholderText("DAK H2/3 ctrl3+")
            eh.setToolTip("Holdings: 'DAK', 'HQxx', 'Sxx'; 'H2/3' = 2 of top 3; "
                          "'ctrl3-5' = controls")
            g.addWidget(eh, i, 7); self.hon[seat] = eh
            pick = QPushButton("Cards…"); pick.setMaximumWidth(64)
            pick.setToolTip("Pick a fixed hand — cards used elsewhere are blocked")
            pick.clicked.connect(lambda _=False, s=seat: self._pick_cards(s))
            g.addWidget(pick, i, 8)
        g.setColumnStretch(2, 1)
        root.addWidget(hands)

        opt = QGroupBox("Options")
        o = QHBoxLayout(opt); o.setContentsMargins(10, 8, 10, 8)
        o.addWidget(QLabel("Us"))
        self.side = QComboBox(); self.side.addItems(["NS", "EW"])
        self.side.setCurrentText(DEFAULT_SIDE)
        self.side.setToolTip("Which side is 'us'; both sides are analysed either way")
        o.addWidget(self.side)
        o.addSpacing(10); o.addWidget(QLabel("Vul"))
        self.vul = QComboBox(); self.vul.addItems([VUL_LABEL[v] for v in VUL_STATES])
        self.vul.setCurrentText(VUL_LABEL[DEFAULT_VUL])
        self.vul.setToolTip("Board vulnerability — each side scored at its own")
        o.addWidget(self.vul)
        o.addSpacing(10); o.addWidget(QLabel("Deals"))
        self.ndeals = QSpinBox(); self.ndeals.setRange(100, 1_000_000)
        self.ndeals.setSingleStep(1000); self.ndeals.setValue(DEFAULT_DEALS)
        self.ndeals.setGroupSeparatorShown(True); o.addWidget(self.ndeals)
        o.addSpacing(10); o.addWidget(QLabel("Seed"))
        self.seed = QLineEdit(); self.seed.setMaximumWidth(70); o.addWidget(self.seed)
        o.addSpacing(10)
        self.samples_cb = QCheckBox("samples"); self.samples_cb.setChecked(False)
        o.addWidget(self.samples_cb)
        self.auto_cb = QCheckBox("🧠 auto")
        self.auto_cb.setToolTip("Ask Claude automatically when a run finishes")
        o.addWidget(self.auto_cb)
        self.finesse_cb = QCheckBox("confidence")
        self.finesse_cb.setChecked(False)
        self.finesse_cb.setToolTip("Card-placement / finesse confidence (re-solves with the "
                                   "defenders swapped) — ~2x slower; uncheck for speed")
        o.addWidget(self.finesse_cb)
        o.addStretch(1)
        root.addWidget(opt)

        auc_row = QHBoxLayout()
        auc_lbl = QLabel("Auction"); auc_lbl.setStyleSheet("color:#888;")
        auc_row.addWidget(auc_lbl)
        auc_row.addWidget(QLabel("dealer"))
        self.dealer = QComboBox(); self.dealer.addItems(ORDER)
        self.dealer.setCurrentText(DEFAULT_DEALER)
        self.dealer.setToolTip("Who makes the first call")
        auc_row.addWidget(self.dealer)
        self.auction = QLineEdit(); self.auction.setText(DEFAULT_AUCTION)
        self.auction.setPlaceholderText(
            "calls e.g. 1D P 1H P 4H P P P — fixes who declares; blank = best declarer")
        self.auction.setToolTip(
            "Space-separated calls from the dealer (P=pass, X=dbl, XX=rdbl). The final "
            "contract is scored from the seat that actually declares it, with the "
            "'wrong side' cost shown.")
        auc_row.addWidget(self.auction, 1)
        root.addLayout(auc_row)

        act = QHBoxLayout()
        self.run_btn = QPushButton("Run"); self.run_btn.setDefault(True)
        self.run_btn.clicked.connect(self.run); act.addWidget(self.run_btn)
        self.stop_btn = QPushButton("Stop"); self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop); act.addWidget(self.stop_btn)
        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setToolTip("Restore the default hands and clear the report")
        self.reset_btn.clicked.connect(self._reset); act.addWidget(self.reset_btn)
        self.save_btn = QPushButton("Save…")
        self.save_btn.clicked.connect(self._save); self.save_btn.setEnabled(False)
        act.addWidget(self.save_btn)
        self.browser_btn = QPushButton("Open in browser")
        self.browser_btn.clicked.connect(self._browser); self.browser_btn.setEnabled(False)
        act.addWidget(self.browser_btn)
        self.explain_btn = QPushButton("🧠 Explain")
        self.explain_btn.clicked.connect(self._explain); self.explain_btn.setEnabled(False)
        act.addWidget(self.explain_btn)
        self.prog = QLabel(""); self.prog.setStyleSheet("color:#0a7;")
        act.addWidget(self.prog); act.addStretch(1)
        root.addLayout(act)

        ask_row = QHBoxLayout()
        ask_lbl = QLabel("Ask Claude"); ask_lbl.setStyleSheet("color:#888;")
        ask_row.addWidget(ask_lbl)
        self.ask = QLineEdit()
        self.ask.setText(DEFAULT_ASK)
        self.ask.setPlaceholderText(
            "what to analyse — blank = standard bid/stop verdict")
        self.ask.returnPressed.connect(self._explain)
        ask_row.addWidget(self.ask, 1)
        root.addLayout(ask_row)

        # Suggested questions — filled in after a run, click to ask.
        sug_row = QHBoxLayout()
        sl = QLabel("Try"); sl.setStyleSheet("color:#888;"); sug_row.addWidget(sl)
        self.chip_btns = []
        self._chip_q = [""] * 4
        for i in range(4):
            b = QPushButton("")
            b.setStyleSheet(
                "QPushButton{color:#5a86c5;border:1px solid #4a4a4a;border-radius:11px;"
                "padding:2px 11px;} QPushButton:hover{border-color:#5a86c5;}")
            b.clicked.connect(lambda _=False, i=i: self._use_suggestion(i))
            b.hide(); self.chip_btns.append(b); sug_row.addWidget(b)
        sug_row.addStretch(1)
        root.addLayout(sug_row)

        # Build-your-own report: toggle each section on/off.
        rep_row = QHBoxLayout()
        rl = QLabel("Report"); rl.setStyleSheet("color:#888;"); rep_row.addWidget(rl)
        self.sec_cb = {}
        SECTIONS = [("tiles", "summary"), ("auction", "auction"), ("hands", "hands"),
                    ("tables", "make-rates"), ("competitive", "par/compete"),
                    ("breakdown", "breakdown"), ("finesse", "card-play"),
                    ("samples", "samples")]
        for key, label in SECTIONS:
            cb = QCheckBox(label); cb.setChecked(key in REPORT_DEFAULT)
            cb.stateChanged.connect(self._on_sections)
            self.sec_cb[key] = cb; rep_row.addWidget(cb)
        rep_row.addStretch(1)
        root.addLayout(rep_row)

        self.tabs = QTabWidget()
        self.report = QWebEngineView()
        self.report.setHtml(self._placeholder("Run a simulation to see the report."))
        self.tabs.addTab(self.report, "Report")
        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setFrameShape(QFrame.NoFrame)
        self.log.setStyleSheet("font-family:Consolas,monospace;font-size:12px;")
        self.tabs.addTab(self.log, "Log")

        # Suit play — a suit-combination calculator ("best way to play a suit").
        suit_tab = QWidget(); sv = QVBoxLayout(suit_tab)
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Suit"))
        self.suit_top = QLineEdit(); self.suit_top.setPlaceholderText("AKxxx")
        self.suit_top.setMaximumWidth(150); self.suit_top.returnPressed.connect(self._analyse_suit)
        srow.addWidget(self.suit_top)
        srow.addWidget(QLabel("opposite"))
        self.suit_bot = QLineEdit(); self.suit_bot.setPlaceholderText("Qxxx")
        self.suit_bot.setMaximumWidth(150); self.suit_bot.returnPressed.connect(self._analyse_suit)
        srow.addWidget(self.suit_bot)
        pk = QPushButton("Cards…"); pk.clicked.connect(self._pick_suit); srow.addWidget(pk)
        ab = QPushButton("Analyse"); ab.clicked.connect(self._analyse_suit); srow.addWidget(ab)
        mb = QPushButton("From my hands"); mb.clicked.connect(self._suits_from_hands)
        srow.addWidget(mb)
        srow.addStretch(1)
        sv.addLayout(srow)
        self.suit_view = QTextBrowser()
        self._suit_placeholder = (
            "<p style='color:#888'>Type a suit combination above (e.g. "
            "<b>AKxxx</b> opposite <b>Qxxx</b>) and Analyse — or <b>From my hands</b> "
            "after a run to break down each of your suits. x = a low spot.</p>")
        self.suit_view.setHtml(self._suit_placeholder)
        sv.addWidget(self.suit_view, 1)
        self.tabs.addTab(suit_tab, "Suit play")

        root.addWidget(self.tabs, 1)

        for seat in ORDER:
            self._sync(seat)

    def _placeholder(self, msg):
        from ..domain.types import SimResult
        html = render_html(SimResult(config=None, accepted=0, tries=0), self.theme)
        return html.replace("No qualifying deals", msg).replace(
            "The constraints may be impossible — loosen the HCP range or the shape and run again.",
            "The styled report renders here after a run.")

    # ---------------------------------------------------------------- theme
    def _toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
        apply_palette(QApplication.instance(), self.theme)
        if self.last_result:
            self._render()
        else:
            self.report.setHtml(self._placeholder("Run a simulation to see the report."))

    # ---------------------------------------------------------------- sync
    def _sync(self, seat):
        m = self.mode[seat].currentText()
        self.hand[seat].setEnabled(m == "Fixed")
        con = m == "Constrain"
        for w in (self.hlo[seat], self.hhi[seat], self.shp[seat], self.hon[seat]):
            w.setEnabled(con)

    def _vul_state(self):
        label = self.vul.currentText()
        return next((v for v in VUL_STATES if VUL_LABEL[v] == label), "None")

    def _reset(self):
        for seat in ORDER:
            m, hnd, lo, hi, shp = DEFAULTS[seat]
            self.mode[seat].setCurrentText(m)
            self.hand[seat].setText(hnd)
            self.hlo[seat].setValue(lo); self.hhi[seat].setValue(hi)
            self.shp[seat].setText(shp)
            self.hon[seat].clear()
            self._sync(seat)
        self.side.setCurrentText(DEFAULT_SIDE)
        self.vul.setCurrentText(VUL_LABEL[DEFAULT_VUL])
        self.ndeals.setValue(DEFAULT_DEALS)
        self.dealer.setCurrentText(DEFAULT_DEALER)
        self.auction.setText(DEFAULT_AUCTION)
        self.seed.clear()
        self.samples_cb.setChecked(False)
        self.auto_cb.setChecked(False)
        self.finesse_cb.setChecked(False)
        self.ask.setText(DEFAULT_ASK)
        self.last_result = None
        self.last_html = None
        self._answer = ""
        self._question = ""
        for b in self.chip_btns:
            b.hide()
        for key, cb in self.sec_cb.items():
            cb.setChecked(key in REPORT_DEFAULT)
        for b in (self.save_btn, self.browser_btn, self.explain_btn):
            b.setEnabled(False)
        # Suit-play tab back to its blank state, view back to the report.
        self.suit_top.clear(); self.suit_bot.clear()
        self.suit_view.setHtml(self._suit_placeholder)
        self.report.setHtml(self._placeholder("Run a simulation to see the report."))
        self._set_log("")
        self.tabs.setCurrentWidget(self.report)
        self.prog.setText("reset.")

    def _cards_of(self, text):
        """Lenient parse of a (possibly partial) hand -> set of (suit, rank)."""
        cards = set()
        toks = (text.split() + ["", "", "", ""])[:4]
        for suit, tok in zip(SUITS, toks):
            try:
                for r in parse_suit(tok):
                    cards.add((suit, r))
            except ValueError:
                pass
        return cards

    def _pick_cards(self, seat):
        used = {}
        for s in ORDER:
            if s != seat and self.mode[s].currentText() == "Fixed":
                for c in self._cards_of(self.hand[s].text()):
                    used[c] = s
        dlg = CardPicker(self, seat, self._cards_of(self.hand[seat].text()), used)
        if dlg.exec():
            self.hand[seat].setText(dlg.hand_string())
            self.mode[seat].setCurrentText("Fixed")
            self._sync(seat)

    # ---------------------------------------------------------------- run
    def run(self):
        raw = {seat: {"mode": self.mode[seat].currentText(),
                      "hand": self.hand[seat].text(),
                      "lo": self.hlo[seat].value(), "hi": self.hhi[seat].value(),
                      "shape": self.shp[seat].text(),
                      "honors": self.hon[seat].text()} for seat in ORDER}
        try:
            specs = build_specs(raw)
        except ValueError as e:
            self._set_log(f"⚠  {e}"); self.tabs.setCurrentWidget(self.log)
            return

        n = self.ndeals.value()
        smart = smart_seat(specs)
        rej_present = any(
            sp.kind == "con" and ((seat != smart and sp.constrains) or sp.has_honors)
            for seat, sp in specs.items())
        max_tries = max(n * 500, 2_000_000) if rej_present else n
        config = SimConfig(
            specs=specs, n=n, max_tries=max_tries, seed=self.seed.text().strip(),
            side=self.side.currentText(), vul=self._vul_state(),
            n_samples=6 if self.samples_cb.isChecked() else 0,
            finesse=self.finesse_cb.isChecked(),
            dealer=self.dealer.currentText(), auction=self.auction.text().strip())

        # Clear the previous analysis so stale results aren't shown mid-run.
        self.last_result = None
        self.last_html = None
        self.save_btn.setEnabled(False)
        self.browser_btn.setEnabled(False)
        self.report.setHtml(self._placeholder("Simulating…"))
        self._set_log("preparing…")
        self.run_btn.setEnabled(False); self.stop_btn.setEnabled(True)
        self.explain_btn.setEnabled(False); self.prog.setText("preparing…")

        self.sim = SimWorker(config)
        self.sim.progressed.connect(self._on_prog)
        self.sim.finished_ok.connect(self._on_done)
        self.sim.failed.connect(self._on_fail)
        self.sim.aborted.connect(self._on_abort)
        self.sim.start()

    def stop(self):
        if self.sim:
            self.sim.stop()

    def _on_prog(self, a, t):
        rate = f"  ({100*a/t:.1f}% accepted)" if t else ""
        self.prog.setText(f"simulating…  {a} deals / {t} tries{rate}")

    def _on_fail(self, msg):
        self._set_log(f"Error: {msg}"); self.tabs.setCurrentWidget(self.log)
        self._finish()

    def _on_abort(self):
        self._append_log("\nStopped."); self._finish()

    def _have_ai(self):
        return bool(HAVE_ANTHROPIC and os.environ.get("ANTHROPIC_API_KEY"))

    def _on_done(self, result):
        self.last_result = result if not result.empty else None
        self._set_log(render_text(result))
        q = self.ask.text().strip()
        # A typed question is answered on top of the report automatically.
        self._question = q if (q and self.last_result and self._have_ai()) else ""
        self._answer = ""
        self._update_suggestions()
        if result.empty:
            self.report.setHtml(render_html(result, self.theme))
        else:
            self._render()
        self._finish("done")

    def _finish(self, why=""):
        self.run_btn.setEnabled(True); self.stop_btn.setEnabled(False)
        if why == "done":
            self.prog.setText("done.")
            self.browser_btn.setEnabled(self.last_html is not None)
            self.save_btn.setEnabled(self.last_result is not None)
            if self.last_result and self._have_ai():
                self.explain_btn.setEnabled(True)
                if self._question:            # answer the typed question, on top
                    self._answer_on_top()
                elif self.auto_cb.isChecked():
                    self._explain()

    def _show_set(self):
        return {k for k, cb in self.sec_cb.items() if cb.isChecked()}

    def _render(self):
        self.last_html = render_html(self.last_result, self.theme,
                                     answer=self._answer or None,
                                     question=self._question or None,
                                     show=self._show_set())
        self.report.setHtml(self.last_html)
        self.tabs.setCurrentWidget(self.report)

    def _on_sections(self):
        if self.last_result:
            self._render()

    # -------------------------------------------------- suggestions
    def _sym(self, label):
        if label.endswith("NT") or not label[-1:].isalpha():
            return label
        return label[:-1] + SUIT_SYM.get(label[-1], label[-1])

    def _suggestions(self, r):
        out = []
        a = r.auction
        if a:
            c, dd = a.contract, "x" * a.doubled
            out.append((f"odds {self._sym(c)}{dd} makes",
                        f"what are the odds of {c}{dd} making?"))
            out.append(("how many off?",
                        f"how many tricks does {c}{dd} go off, and how often?"))
            if not a.on_our_side:
                out.append(("beat it how often?",
                            f"how often do we beat {c}{dd}, and by how much?"))
        bg, bs = r.best_game, r.best_slam
        if bs and bs.make_rate >= 15 and len(out) < 4:
            out.append((f"how safe is {self._sym(bs.label)}?",
                        f"how safe is {bs.label} — what are the odds it makes?"))
        if bg and len(out) < 4:
            out.append((f"odds {self._sym(bg.label)}",
                        f"what are the odds of {bg.label} making?"))
        return out[:4]

    def _update_suggestions(self):
        sug = self._suggestions(self.last_result) if self.last_result else []
        for i, b in enumerate(self.chip_btns):
            if i < len(sug):
                b.setText(sug[i][0]); self._chip_q[i] = sug[i][1]; b.show()
            else:
                b.hide(); self._chip_q[i] = ""

    def _use_suggestion(self, i):
        q = self._chip_q[i] if i < len(self._chip_q) else ""
        if not q:
            return
        self.ask.setText(q)
        if self.last_result and self._have_ai():
            self._question = q
            self._answer_on_top()

    # -------------------------------------------------- suit play
    def _suit_html(self, title, r):
        mt = r["max_tricks"]
        cols = [k for k in range(mt, max(mt - 3, 0), -1)]

        def row(label, cum, strong=False):
            w = "font-weight:600;" if strong else ""
            cells = "".join(f"<td style='text-align:right;padding:2px 0 2px 18px;{w}'>"
                            f"{cum.get(k, 0):.0f}%</td>" for k in cols)
            return f"<tr><td style='padding:2px 0;{w}'>{label}</td>{cells}</tr>"

        hdr = "".join(f"<td style='text-align:right;color:#888;font-size:11px;padding-left:18px'>"
                      f"{k} trick{'s' if k != 1 else ''}</td>" for k in cols)
        head = (f"<h3 style='margin:16px 0 1px'>{title}</h3>"
                f"<div style='color:#888;font-size:12px;margin-bottom:6px'>"
                f"{r['top'] or '—'} opposite {r['bottom'] or '—'} · defenders hold {r['missing']} "
                f"· chance of <b>at least</b> N tricks, real odds vs best defence</div>")
        if r["no_guess"]:
            return head + (f"<table cellspacing='0'><tr><td></td>{hdr}</tr>"
                           f"{row('cash it — no guess', r['lines']['drop']['cum'], True)}</table>")
        rec = r["best"]
        dl = "play for the drop" + ("  ✓" if rec == "drop" else "")
        fl = "take the finesse" + ("  ✓" if rec == "finesse" else "")
        return head + (f"<table cellspacing='0'><tr><td></td>{hdr}</tr>"
                       f"{row(dl, r['lines']['drop']['cum'], rec == 'drop')}"
                       f"{row(fl, r['lines']['finesse']['cum'], rec == 'finesse')}"
                       f"{row('perfect-guess ceiling', r['ceiling'])}"
                       f"</table>")

    def _suit_html_opt(self, title, r):
        mt = r["max_tricks"]
        cols = [k for k in range(mt, max(mt - 3, 0), -1)]
        hdr = "".join(f"<td style='text-align:right;color:#888;font-size:11px;padding-left:20px'>"
                      f"{k} trick{'s' if k != 1 else ''}</td>" for k in cols)
        cells = "".join(f"<td style='text-align:right;padding:2px 0 2px 20px;font-weight:600'>"
                        f"{r['cum'].get(k, 0):.0f}%</td>" for k in cols)
        play = r.get("play", "")
        play_html = (f"<div style='margin:5px 0 9px;font-size:14px'>"
                     f"<b style='color:#5a86c5'>Play:</b> {play}</div>") if play else ""
        if r.get("ceiling"):
            basis = ("<b>double-dummy odds</b> (best-case; the exact blind-play "
                     "solve is too costly for this holding)")
        elif r.get("exact"):
            basis = "<b>exact real odds</b> (best line vs best defence)"
        else:
            basis = "<b>real odds</b> vs best defence (estimate, within ~1%)"
        return (f"<h3 style='margin:16px 0 1px'>{title}</h3>"
                f"<div style='color:#888;font-size:12px;margin-bottom:6px'>"
                f"{r['top'] or '—'} opposite {r['bottom'] or '—'} · defenders hold {r['missing']} · "
                f"{basis}</div>"
                f"{play_html}"
                f"<table cellspacing='0'><tr><td></td>{hdr}</tr>"
                f"<tr><td style='padding-right:6px'>chance of at least</td>{cells}</tr></table>")

    def _pick_suit(self):
        dlg = SuitPicker(self, self.suit_top.text().strip(), self.suit_bot.text().strip())
        if dlg.exec():
            top, bot = dlg.holdings()
            self.suit_top.setText(top); self.suit_bot.setText(bot)
            if top or bot:
                self._start_suits([("Best play", top, bot)])

    def _analyse_suit(self):
        top, bot = self.suit_top.text().strip(), self.suit_bot.text().strip()
        if top or bot:
            self._start_suits([("Best play", top, bot)])

    def _start_suits(self, items):
        self.suit_view.setHtml("<p style='color:#888'>Solving optimal play… "
                               "(a few seconds on two-honour suits)</p>")
        self.suit_worker = SuitWorker(items)
        self.suit_worker.done.connect(self._render_suits)
        self.suit_worker.start()

    def _render_suits(self, results):
        html = ""
        for title, r, is_opt in results:
            if "error" in r:
                html += f"<p style='color:#b00'>{title}: {r['error']}</p>"
            elif is_opt:
                html += self._suit_html_opt(title, r)
            else:
                html += self._suit_html(title, r)
        self.suit_view.setHtml(html or "<p style='color:#888'>Nothing to analyse.</p>")

    def _side_suits(self, result):
        if not result or not result.config:
            return None
        specs = result.config.specs
        sp1, sp2 = specs.get(result.side[0]), specs.get(result.side[1])
        if not (sp1 and sp1.kind == "fixed" and sp2 and sp2.kind == "fixed"):
            return None
        t = (sp1.fixed.split() + ["", "", "", ""])[:4]
        b = (sp2.fixed.split() + ["", "", "", ""])[:4]
        return [(s, t[i], b[i]) for i, s in enumerate(("♠", "♥", "♦", "♣"))]

    def _suits_from_hands(self):
        combos = self._side_suits(self.last_result)
        if not combos:
            self.suit_view.setHtml("<p style='color:#888'>Set both of your hands to "
                                   "<b>Fixed</b> and Run first, then this breaks down each suit.</p>")
            return
        items = [(f"{sym}  {top or '—'} / {bot or '—'}", top, bot)
                 for sym, top, bot in combos if len(top) + len(bot) >= 5]
        if not items:
            self.suit_view.setHtml("<p style='color:#888'>No long suits to analyse "
                                   "(short suits are skipped).</p>")
            return
        self._start_suits(items)

    # -------------------------------------------------- answer on top
    def _answer_on_top(self):
        self.explain_btn.setEnabled(False)
        self.prog.setText("asking Claude…")
        self._answer = ""
        self._render()                        # shows the "Asking Claude…" banner
        self.ai = AiWorker(build_prompt(self.last_result, self._question))
        self.ai.chunk.connect(self._answer_chunk)
        self.ai.finished_ok.connect(self._answer_done)
        self.ai.failed.connect(self._ai_fail)
        self.ai.start()

    def _answer_chunk(self, s):
        self._answer += s

    def _answer_done(self):
        self._render()                        # re-render with the full answer on top
        self.prog.setText("done.")
        self.explain_btn.setEnabled(True)

    # ---------------------------------------------------------------- log
    def _set_log(self, s):
        self.log.setPlainText(s)

    def _append_log(self, s):
        self.log.moveCursor(QTextCursor.MoveOperation.End)
        self.log.insertPlainText(s)
        self.log.ensureCursorVisible()

    def _save(self):
        if not self.last_result:
            self.prog.setText("run a simulation first."); return
        side = self.last_result.side
        default = f"bridge-mc-{side}-{self.last_result.accepted}deals.html"
        path, flt = QFileDialog.getSaveFileName(
            self, "Save results", default,
            "HTML report (*.html);;Text report (*.txt)")
        if not path:
            return
        low = path.lower()
        if low.endswith(".txt") or (not low.endswith(".html") and "Text" in flt):
            data = render_text(self.last_result)
        else:
            data = self.last_html or render_html(self.last_result, self.theme)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
            self.prog.setText(f"saved {os.path.basename(path)}")
        except OSError as e:
            self.prog.setText(f"save failed: {e}")

    def _browser(self):
        if not self.last_html:
            self.prog.setText("run a simulation first."); return
        f = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False,
                                        encoding="utf-8")
        f.write(self.last_html); f.close()
        webbrowser.open("file:///" + f.name.replace("\\", "/"))

    # ---------------------------------------------------------------- AI
    def _explain(self):
        if not self.last_result:
            return
        q = self.ask.text().strip()
        if q:                              # a question -> answer on top of the report
            self._question = q
            self._answer_on_top()
            return
        # blank -> stream the standard verdict into the Log
        self.explain_btn.setEnabled(False)
        self.prog.setText("asking Claude…")
        self.tabs.setCurrentWidget(self.log)
        self._append_log("\n\n── AI verdict ──\n")
        self.ai = AiWorker(build_prompt(self.last_result, ""))
        self.ai.chunk.connect(self._append_log)
        self.ai.finished_ok.connect(lambda: (self.prog.setText("AI verdict done."),
                                             self.explain_btn.setEnabled(True)))
        self.ai.failed.connect(self._ai_fail)
        self.ai.start()

    def _ai_fail(self, msg):
        self.prog.setText("AI error."); self.explain_btn.setEnabled(True)
        if self._question:
            self._answer = f"(couldn't reach Claude: {msg})"
            self._render()
        else:
            self._append_log(f"\n[AI error: {msg}]\n")


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("Bridge MC Simulator")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
