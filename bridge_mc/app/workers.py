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

    def __init__(self, items):        # items: list[(title, top, bot)]
        super().__init__()
        self.items = items

    def run(self):
        from ..domain.suitplay_opt import suit_optimal
        out = []
        for title, top, bot in self.items:
            try:
                # Always returns a usable result: exact single-dummy where it can
                # solve in time, else the verified double-dummy ceiling.
                out.append((title, suit_optimal(top, bot), True))
            except Exception as e:    # noqa: BLE001
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
