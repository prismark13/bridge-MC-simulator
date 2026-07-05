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
    QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ..ai import HAVE_ANTHROPIC, build_prompt
from ..domain import (
    ORDER, SUITS, VUL_LABEL, VUL_STATES, SimConfig, build_specs, parse_suit)
from ..engine.sampling import smart_seat
from ..report import render_html, render_text
from .card_picker import CardPicker
from .theming import apply_palette
from .workers import AiWorker, SimWorker

DEFAULTS = {
    "N": ("Constrain", "", 11, 18, "3-5 3-5 1-3 0"),
    "E": ("Random", "", 0, 37, "any"),
    "S": ("Fixed", "876 AJT65 A7 K76", 11, 14, "5 3 0 0"),
    "W": ("Constrain", "", 3, 10, "0 0 5-7 0"),
}
DEFAULT_SIDE = "NS"
DEFAULT_VUL = "Both"
DEFAULT_DEALS = 2000
DEFAULT_ASK = "should South bid over 5H by North"
MODES = ["Random", "Fixed", "Constrain"]


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
        self.samples_cb = QCheckBox("samples"); self.samples_cb.setChecked(True)
        o.addWidget(self.samples_cb)
        self.auto_cb = QCheckBox("🧠 auto")
        self.auto_cb.setToolTip("Ask Claude automatically when a run finishes")
        o.addWidget(self.auto_cb)
        o.addStretch(1)
        root.addWidget(opt)

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

        self.tabs = QTabWidget()
        self.report = QWebEngineView()
        self.report.setHtml(self._placeholder("Run a simulation to see the report."))
        self.tabs.addTab(self.report, "Report")
        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setFrameShape(QFrame.NoFrame)
        self.log.setStyleSheet("font-family:Consolas,monospace;font-size:12px;")
        self.tabs.addTab(self.log, "Log")
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
        self.seed.clear()
        self.samples_cb.setChecked(True)
        self.auto_cb.setChecked(False)
        self.ask.setText(DEFAULT_ASK)
        self.last_result = None
        self.last_html = None
        for b in (self.save_btn, self.browser_btn, self.explain_btn):
            b.setEnabled(False)
        self.report.setHtml(self._placeholder("Run a simulation to see the report."))
        self._set_log("")
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
            n_samples=6 if self.samples_cb.isChecked() else 0)

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

    def _on_done(self, result):
        self.last_result = result if not result.empty else None
        self._set_log(render_text(result))
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
            if (self.last_result and HAVE_ANTHROPIC
                    and os.environ.get("ANTHROPIC_API_KEY")):
                self.explain_btn.setEnabled(True)
                if self.auto_cb.isChecked():
                    self._explain()

    def _render(self):
        self.last_html = render_html(self.last_result, self.theme)
        self.report.setHtml(self.last_html)
        self.tabs.setCurrentWidget(self.report)

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
        self.explain_btn.setEnabled(False)
        self.prog.setText("asking Claude…")
        self.tabs.setCurrentWidget(self.log)
        question = self.ask.text().strip()
        header = f"── AI: {question} ──" if question else "── AI verdict ──"
        self._append_log(f"\n\n{header}\n")
        self.ai = AiWorker(build_prompt(self.last_result, question))
        self.ai.chunk.connect(self._append_log)
        self.ai.finished_ok.connect(lambda: (self.prog.setText("AI verdict done."),
                                             self.explain_btn.setEnabled(True)))
        self.ai.failed.connect(self._ai_fail)
        self.ai.start()

    def _ai_fail(self, msg):
        self._append_log(f"\n[AI error: {msg}]\n")
        self.prog.setText("AI error."); self.explain_btn.setEnabled(True)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("Bridge MC Simulator")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
