"""Qt threads that drive the (UI-agnostic) engine and AI stream."""
from PySide6.QtCore import QThread, Signal

from ..ai import stream_explanation
from ..engine import run
from ..engine.simulate import Aborted


class SimWorker(QThread):
    progressed = Signal(int, int)
    finished_ok = Signal(object)      # SimResult
    failed = Signal(str)
    aborted = Signal()

    def __init__(self, config):
        super().__init__()
        self.config = config
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            res = run(self.config, stop=lambda: self._stop,
                      progress=lambda a, t: self.progressed.emit(a, t))
            self.finished_ok.emit(res)
        except Aborted:
            self.aborted.emit()
        except Exception as e:
            self.failed.emit(repr(e))


class SuitWorker(QThread):
    """Solve one or more suit combinations off the UI thread (the optimal solver
    can take a few seconds on two-honour holdings)."""
    done = Signal(list)               # list[(title, result_dict, is_optimal)]

    def __init__(self, items, entries=None, start="F"):
        super().__init__()               # items: list[(title, top, bot)]
        self.items = items
        self.entries = entries           # None = unlimited, else (eN, eS)
        self.start = start

    def run(self):
        # vec-prop (Frank, Basin & Bundy, AAAI 2000): exact on every holding and
        # ~1000x faster than the old information-set minimax, which searched over
        # partitions of the information set and blew up. Validated against
        # SuitPlay to four decimals, including the holdings the old one couldn't
        # solve at all. It carries its own line, read off the winning strategy.
        from ..domain.suitplay_vec import suit_vec, Timeout
        out = []
        for title, top, bot in self.items:
            try:
                out.append((title, suit_vec(top, bot, time_budget=25.0,
                                            entries=self.entries,
                                            start=self.start), True))
            except Timeout:
                if self.entries is not None:   # no non-entry fallback would be right
                    out.append((title, {"error": "Timed out with entry limits — "
                                        "try the full-entry solve."}, False))
                else:
                    from ..domain.suitplay_opt import suit_optimal
                    out.append((title, suit_optimal(top, bot), True))
            except Exception as e:         # noqa: BLE001
                out.append((title, {"error": str(e)}, False))
        self.done.emit(out)


class AiWorker(QThread):
    chunk = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, prompt):
        super().__init__()
        self.prompt = prompt

    def run(self):
        try:
            for text in stream_explanation(self.prompt):
                self.chunk.emit(text)
            self.finished_ok.emit()
        except Exception as e:
            self.failed.emit(repr(e))
