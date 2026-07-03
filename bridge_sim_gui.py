"""
Bridge slam/game Monte-Carlo GUI.

You (South) hold a fixed hand; partner (North) is constrained by HCP + shape.
Deals are generated with Redeal (smartstack when possible) and solved
double-dummy with the bundled DDS engine.  The tool reports how often every
game and slam makes, with 95% confidence intervals.
"""
import ctypes
import random
import queue
import threading
import tkinter as tk
from ctypes import Structure, POINTER, byref, c_byte, c_int
from tkinter import ttk

from redeal import Deal, H, SmartStack, balanced, semibalanced, hcp
from redeal import dds as _rdds

# ---------------------------------------------------------------------------
# Fast double-dummy: call DDS's batched CalcAllTables directly through the
# dds.dll that Redeal already loaded (solves 32 full tables per call across
# all cores -- ~6x faster than Redeal's one-contract-at-a-time solver).
# ---------------------------------------------------------------------------
BATCH = 32
_SI = {"S": 0, "H": 1, "D": 2, "C": 3, "N": 4}   # strain index in resTable


class _TDeal(Structure):
    _fields_ = [("cards", c_int * 4 * 4)]


class _TDeals(Structure):
    _fields_ = [("noOfTables", c_int), ("deals", _TDeal * BATCH)]


class _TRes(Structure):
    _fields_ = [("resTable", c_int * 4 * 5)]


class _TablesRes(Structure):
    _fields_ = [("noOfBoards", c_int), ("results", _TRes * BATCH)]


class _ParBuf(Structure):           # scratch space DDS wants; we ignore par
    _fields_ = [("_b", c_byte * (BATCH * 1024))]


_rdds.dll.CalcAllTables.argtypes = [
    POINTER(_TDeals), c_int, c_int * 5, POINTER(_TablesRes), POINTER(_ParBuf)]
try:
    _rdds.dll.SetMaxThreads(0)
except Exception:                   # non-fatal; default threading still works
    pass
_TRUMP_FILTER = (c_int * 5)(0, 0, 0, 0, 0)   # 0 = compute this strain
_PAR = _ParBuf()


def solve_batch(deals):
    """Double-dummy-solve a list (<=BATCH) of Redeal deals.
    Returns a list of dicts strain -> best NS declarer tricks."""
    dd = _TDeals(); dd.noOfTables = len(deals)
    for i, deal in enumerate(deals):
        for seat, hand in enumerate(deal):
            for suit, holding in enumerate(hand):
                dd.deals[i].cards[seat][suit] = \
                    sum(1 << r.value for r in holding)
    res = _TablesRes()
    if _rdds.dll.CalcAllTables(byref(dd), -1, _TRUMP_FILTER, byref(res),
                               byref(_PAR)) != 1:
        raise RuntimeError("CalcAllTables failed")
    out = []
    for i in range(len(deals)):
        rt = res.results[i].resTable
        # best of North (0) / South (2) declarer per strain
        out.append({s: max(rt[_SI[s]][0], rt[_SI[s]][2]) for s in STRAINS})
    return out


# ---------------------------------------------------------------------------
# Simulation core (no Tk in here, so it can be tested headlessly)
# ---------------------------------------------------------------------------
SUITS = ["S", "H", "D", "C"]
SUIT_SYM = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RED = {"H", "D"}
RANKS = "AKQJT98765432"
STRAINS = ["C", "D", "H", "S", "N"]          # N = notrump

# game/slam trick thresholds per strain
GAMES = [("3NT", "N", 9), ("4H", "H", 10), ("4S", "S", 10),
         ("5C", "C", 11), ("5D", "D", 11)]
SLAMS = [("6C", "C", 12), ("6D", "D", 12), ("6H", "H", 12),
         ("6S", "S", 12), ("6NT", "N", 12)]


def parse_suit(tok):
    """Return an uppercased, validated rank string for one suit (may be '')."""
    t = tok.strip().upper().replace("10", "T")
    if t in ("", "-", "VOID"):
        return ""
    bad = [c for c in t if c not in RANKS]
    if bad:
        raise ValueError(f"invalid card(s) {''.join(bad)!r} (use A K Q J T 9..2)")
    if len(set(t)) != len(t):
        raise ValueError("duplicate card in a suit")
    return t


def parse_south(boxes):
    """boxes: dict suit->str.  Returns a Redeal H() hand; must be exactly 13."""
    holding, total = {}, 0
    for s in SUITS:
        try:
            holding[s] = parse_suit(boxes[s])
        except ValueError as e:
            raise ValueError(f"{SUIT_SYM[s]}: {e}")
        total += len(holding[s])
    if total != 13:
        raise ValueError(f"your hand has {total} cards - it must be exactly 13")
    return H(" ".join(holding[s] or "-" for s in SUITS))


def fmt_north(deal):
    n = deal.north
    return " ".join(f"{SUIT_SYM[s]}{h or '-'}" for s, h in
                    zip(SUITS, (n.spades, n.hearts, n.diamonds, n.clubs)))


class Aborted(Exception):
    pass


def simulate(south_hand, lo, hi, mode, mins, n, max_tries, seed,
             n_samples=6, stop=lambda: False, progress=lambda a, t: None):
    """Run the Monte-Carlo; returns (tally, tries, accepted, samples)."""
    if seed not in (None, ""):
        random.seed(int(seed))

    predeal = {"S": south_hand}
    smart = "Balanced" in mode or "Semibalanced" in mode
    if smart:
        shape = balanced if "Balanced" in mode else semibalanced
        predeal["N"] = SmartStack(shape, hcp, range(lo, hi + 1))
    dealer = Deal.prepare(predeal)

    def accept(deal):
        if not (lo <= deal.north.hcp <= hi):
            return False
        sh = deal.north.shape
        return all(sh[i] >= mins[i] for i in range(4))

    keys = [k for k, _, _ in GAMES] + [k for k, _, _ in SLAMS] + \
           ["any game", "any slam", "grand"]
    tally = {k: 0 for k in keys}
    samples = []
    accepted = candidates = tries = 0
    pending = []

    def flush():
        nonlocal accepted
        if not pending:
            return
        for deal, t in zip(pending, solve_batch(pending)):
            made_game = made_slam = False
            for label, st, need in GAMES:
                if t[st] >= need:
                    tally[label] += 1
                    made_game = True
            for label, st, need in SLAMS:
                if t[st] >= need:
                    tally[label] += 1
                    made_slam = True
            tally["any game"] += made_game
            tally["any slam"] += made_slam
            tally["grand"] += max(t.values()) >= 13
            if len(samples) < n_samples:
                samples.append((deal.north.hcp, deal.north.shape,
                                fmt_north(deal), dict(t)))
            accepted += 1
        pending.clear()

    while candidates < n:
        if stop():
            raise Aborted()
        if tries >= max_tries:
            break
        tries += 1
        deal = dealer()
        if not smart and not accept(deal):
            continue
        pending.append(deal)
        candidates += 1
        if len(pending) >= BATCH:
            flush()
            progress(accepted, tries)
    flush()
    return tally, tries, accepted, samples


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class App(tk.Frame):
    def __init__(self, master):
        super().__init__(master, padx=12, pady=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.q = queue.Queue()
        self.worker = None
        self.stop_flag = False
        self._build()

    # ---------------------------------------------------------------- layout
    def _hd(self, text, row):
        tk.Label(self, text=text, font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=8, sticky="w", pady=(8, 2))

    def _build(self):
        r = 0
        self._hd("YOUR HAND (South)", r); r += 1
        self.south = {}
        default = {"S": "9", "H": "J6", "D": "K9753", "C": "QJ654"}
        c = 0
        for s in SUITS:
            tk.Label(self, text=SUIT_SYM[s], font=("Segoe UI", 13),
                     fg="red" if s in RED else "black").grid(row=r, column=c, sticky="e")
            e = tk.Entry(self, width=10); e.insert(0, default[s])
            e.grid(row=r, column=c + 1, padx=(2, 10)); self.south[s] = e; c += 2
        r += 1

        self._hd("PARTNER (North)", r); r += 1
        tk.Label(self, text="HCP").grid(row=r, column=0, sticky="e")
        self.hcp_lo = self._spin(22, 0, 37); self.hcp_lo.grid(row=r, column=1, sticky="w")
        tk.Label(self, text="to").grid(row=r, column=2)
        self.hcp_hi = self._spin(24, 0, 37); self.hcp_hi.grid(row=r, column=3, sticky="w"); r += 1

        tk.Label(self, text="Shape").grid(row=r, column=0, sticky="e")
        self.shape = ttk.Combobox(self, width=26, state="readonly", values=[
            "Balanced (fast)", "Semibalanced (fast)", "Custom min-lengths (filter)"])
        self.shape.set("Balanced (fast)")
        self.shape.bind("<<ComboboxSelected>>", lambda _e: self._sync_mode())
        self.shape.grid(row=r, column=1, columnspan=4, sticky="w"); r += 1

        self.mins_lbl = tk.Label(self, text="Min len ♠♥♦♣")
        self.mins_lbl.grid(row=r, column=0, sticky="e")
        self.mins = tk.Entry(self, width=10); self.mins.insert(0, "0 5 4 0")
        self.mins.grid(row=r, column=1, columnspan=3, sticky="w")
        self.mins_hint = tk.Label(self, text="(Custom mode only)", fg="gray")
        self.mins_hint.grid(row=r, column=4, columnspan=3, sticky="w"); r += 1

        self._hd("RUN", r); r += 1
        tk.Label(self, text="Deals").grid(row=r, column=0, sticky="e")
        self.ndeals = self._spin(5000, 100, 1000000, inc=1000, width=8)
        self.ndeals.grid(row=r, column=1, sticky="w")
        tk.Label(self, text="Seed").grid(row=r, column=2, sticky="e")
        self.seed = tk.Entry(self, width=8); self.seed.grid(row=r, column=3, sticky="w")
        self.samples_var = tk.IntVar(value=1)
        tk.Checkbutton(self, text="sample deals", variable=self.samples_var
                       ).grid(row=r, column=4, columnspan=3, sticky="w"); r += 1

        self.run_btn = tk.Button(self, text="Run", width=10, command=self.run)
        self.run_btn.grid(row=r, column=1, sticky="w")
        self.stop_btn = tk.Button(self, text="Stop", width=8, command=self.stop,
                                  state="disabled")
        self.stop_btn.grid(row=r, column=2)
        tk.Button(self, text="Copy results", width=12,
                  command=self._copy).grid(row=r, column=3, columnspan=2, sticky="w"); r += 1

        self.prog = tk.Label(self, text="", anchor="w", fg="#0a5")
        self.prog.grid(row=r, column=0, columnspan=8, sticky="we"); r += 1

        self.out = tk.Text(self, width=54, height=22, font=("Consolas", 10), wrap="none")
        self.out.grid(row=r, column=0, columnspan=8, sticky="nsew", pady=(6, 0))
        self.out.tag_config("warn", foreground="#c00")
        self.out.tag_config("head", font=("Consolas", 10, "bold"))
        self.rowconfigure(r, weight=1)
        for col in range(8):
            self.columnconfigure(col, weight=1)
        self._sync_mode()

    def _spin(self, val, lo, hi, inc=1, width=4):
        sb = tk.Spinbox(self, from_=lo, to=hi, increment=inc, width=width)
        sb.delete(0, "end"); sb.insert(0, str(val))
        return sb

    def _sync_mode(self):
        custom = "Custom" in self.shape.get()
        self.mins.config(state="normal" if custom else "disabled")
        self.mins_lbl.config(fg="black" if custom else "gray")

    # ------------------------------------------------------------- execution
    def run(self):
        try:
            south = parse_south({s: self.south[s].get() for s in SUITS})
            lo, hi = int(self.hcp_lo.get()), int(self.hcp_hi.get())
            if lo > hi:
                raise ValueError("HCP min is greater than HCP max")
            n = int(self.ndeals.get())
            mode = self.shape.get()
            mins = [0, 0, 0, 0]
            if "Custom" in mode:
                parts = self.mins.get().split()
                if len(parts) != 4:
                    raise ValueError("Min lengths need 4 numbers, e.g. 0 5 4 0")
                mins = [int(x) for x in parts]
                if sum(mins) > 13:
                    raise ValueError(f"min lengths sum to {sum(mins)} (>13) - impossible")
        except ValueError as e:
            self.out.delete("1.0", "end"); self._log(f"!  {e}\n", "warn"); return

        self.out.delete("1.0", "end")
        self.stop_flag = False
        self.run_btn.config(state="disabled"); self.stop_btn.config(state="normal")
        self.prog.config(text="preparing...")
        max_tries = n if "Custom" not in mode else max(n * 500, 2_000_000)
        n_samples = 6 if self.samples_var.get() else 0
        args = (south, lo, hi, mode, mins, n, max_tries,
                self.seed.get().strip(), n_samples)
        self.worker = threading.Thread(target=self._work, args=args, daemon=True)
        self.worker.start()
        self.after(80, self._poll)

    def stop(self):
        self.stop_flag = True

    def _work(self, *a):
        south, lo, hi, mode, mins, n, max_tries, seed, n_samples = a
        try:
            res = simulate(south, lo, hi, mode, mins, n, max_tries, seed,
                           n_samples=n_samples,
                           stop=lambda: self.stop_flag,
                           progress=lambda acc, tr: self.q.put(("prog", acc, tr)))
            self.q.put(("done", res, mode))
        except Aborted:
            self.q.put(("stopped",))
        except Exception as e:            # surface anything to the UI
            self.q.put(("error", repr(e)))

    def _poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                if m[0] == "prog":
                    _, acc, tr = m
                    rate = f"  ({100*acc/tr:.1f}% accepted)" if tr else ""
                    self.prog.config(text=f"simulating... {acc} deals / {tr} tries{rate}")
                elif m[0] == "error":
                    self._log(f"Error: {m[1]}\n", "warn"); self._finish("error")
                elif m[0] == "stopped":
                    self._log("Stopped.\n", "warn"); self._finish("stopped")
                elif m[0] == "done":
                    self._report(*m[1], mode=m[2]); self._finish("done")
        except queue.Empty:
            pass
        if self.worker and self.worker.is_alive():
            self.after(80, self._poll)

    def _finish(self, why):
        self.run_btn.config(state="normal"); self.stop_btn.config(state="disabled")
        if why == "done":
            self.prog.config(text="done.")

    def _report(self, tally, tries, accepted, samples, mode):
        out = self.out
        if accepted == 0:
            self._log("No qualifying deals - the constraint may be impossible "
                      "given your hand.\n", "warn")
            return
        n = accepted
        if "Custom" in mode and accepted < int(self.ndeals.get()):
            self._log(f"!  Only {accepted} deals found in {tries} tries "
                      f"(rare constraint); results are for those.\n\n", "warn")

        def line(label, key):
            p = 100 * tally[key] / n
            se = 1.96 * (p * (100 - p) / n) ** 0.5
            bar = "#" * round(p / 5)
            out.insert("end", f"  {label:<11}{p:5.1f}% +-{se:3.1f}  {bar}\n")

        acc = 100 * accepted / tries if tries else 0
        out.insert("end", f"{accepted} deals  ({tries} tries, {acc:.1f}% accepted)\n")
        out.insert("end", "double-dummy, best NS declarer\n")
        out.insert("end", "\nGAMES\n", "head"); out.insert("end", "-" * 32 + "\n")
        for lab, key, _ in GAMES:
            line(lab, key)
        line("any game", "any game")
        out.insert("end", "\nSLAMS\n", "head"); out.insert("end", "-" * 32 + "\n")
        for lab, key, _ in SLAMS:
            line(lab, key)
        line("any slam", "any slam")
        line("grand(7)", "grand")

        if samples:
            out.insert("end", "\nSAMPLE PARTNER HANDS\n", "head")
            out.insert("end", "-" * 32 + "\n")
            for h, sh, txt, t in samples:
                out.insert("end", f"  {txt}\n    HCP {h}  shape {tuple(sh)}  "
                                  f"tricks C{t['C']} D{t['D']} H{t['H']} "
                                  f"S{t['S']} NT{t['N']}\n")
        out.see("1.0")

    # ----------------------------------------------------------------- utils
    def _log(self, s, tag=None):
        self.out.insert("end", s, tag or ())
        self.out.see("end")

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.out.get("1.0", "end").rstrip())
        self.prog.config(text="results copied to clipboard.")


def main():
    root = tk.Tk()
    root.title("Bridge Slam Simulator  -  Redeal + DDS")
    root.minsize(430, 560)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
