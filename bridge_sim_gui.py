"""
Bridge game/slam Monte-Carlo GUI.

Each seat (N/E/S/W) can be Random, a Fixed hand, or Constrained (HCP range +
shape / min-lengths).  Deals are generated with Redeal (smartstack when one
constrained seat is balanced) and solved double-dummy with the bundled DDS
engine.  Reports how often every NS game and slam makes, with 95% CIs.
"""
import os
import sys

# A frozen windowed app (PyInstaller --windowed) has no console, so stdout and
# stderr are None; any library that writes to them would crash at import.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

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
# Fast double-dummy via DDS's batched CalcAllTables (32 tables/call, all cores)
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


class _ParBuf(Structure):
    _fields_ = [("_b", c_byte * (BATCH * 1024))]


_rdds.dll.CalcAllTables.argtypes = [
    POINTER(_TDeals), c_int, c_int * 5, POINTER(_TablesRes), POINTER(_ParBuf)]
try:
    _rdds.dll.SetMaxThreads(0)
except Exception:
    pass
_TRUMP_FILTER = (c_int * 5)(0, 0, 0, 0, 0)
_PAR = _ParBuf()


def solve_batch(deals):
    """DD-solve up to BATCH deals; return list of dict strain->best NS tricks."""
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
        out.append({s: max(rt[_SI[s]][0], rt[_SI[s]][2]) for s in STRAINS})
    return out


# ---------------------------------------------------------------------------
# Simulation core (no Tk here, so it can be tested headlessly)
# ---------------------------------------------------------------------------
SUITS = ["S", "H", "D", "C"]
SUIT_SYM = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RED = {"H", "D"}
RANKS = "AKQJT98765432"
STRAINS = ["C", "D", "H", "S", "N"]
ORDER = ["N", "E", "S", "W"]
ATTR = {"N": "north", "E": "east", "S": "south", "W": "west"}
SHAPE_TEST = {"bal": balanced, "semibal": semibalanced}

GAMES = [("3NT", "N", 9), ("4H", "H", 10), ("4S", "S", 10),
         ("5C", "C", 11), ("5D", "D", 11)]
SLAMS = [("6C", "C", 12), ("6D", "D", 12), ("6H", "H", 12),
         ("6S", "S", 12), ("6NT", "N", 12)]


class Aborted(Exception):
    pass


def parse_suit(tok):
    t = tok.strip().upper().replace("10", "T")
    if t in ("", "-", "VOID"):
        return ""
    bad = [c for c in t if c not in RANKS]
    if bad:
        raise ValueError(f"invalid card(s) {''.join(bad)!r}")
    if len(set(t)) != len(t):
        raise ValueError("duplicate card in a suit")
    return t


def parse_fixed(text):
    """'AK5 QJT 9432 K8' -> (redeal-hand-string, set of (suit,rank))."""
    toks = text.split()
    if len(toks) != 4:
        raise ValueError("need 4 suits separated by spaces, e.g. 'AK5 QJT 9432 K8'")
    holds, total = {}, 0
    for s, tok in zip(SUITS, toks):
        try:
            holds[s] = parse_suit(tok)
        except ValueError as e:
            raise ValueError(f"{SUIT_SYM[s]}: {e}")
        total += len(holds[s])
    if total != 13:
        raise ValueError(f"{total} cards - a fixed hand needs exactly 13")
    cards = {(s, r) for s in SUITS for r in holds[s]}
    return " ".join(holds[s] or "-" for s in SUITS), cards


def parse_shape(text):
    """Return (kind, mins).  kind in {any, bal, semibal, minlen}."""
    t = text.strip().lower()
    if t in ("", "any"):
        return "any", [0, 0, 0, 0]
    if t in ("bal", "balanced"):
        return "bal", [0, 0, 0, 0]
    if t in ("semi", "semibal", "semibalanced"):
        return "semibal", [0, 0, 0, 0]
    parts = t.split()
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        mins = [int(p) for p in parts]
        if sum(mins) > 13:
            raise ValueError(f"min lengths sum to {sum(mins)} (>13)")
        return "minlen", mins
    raise ValueError("use 'bal', 'semibal', 'any', or 4 min-lengths like '0 5 4 0'")


def _constrains(sp):
    _, lo, hi, kind, mins = sp
    return lo > 0 or hi < 37 or kind != "any" or any(mins)


def _smart_seat(specs):
    """The single seat we can smartstack (first bal/semibal constrained seat)."""
    for seat in ORDER:
        sp = specs[seat]
        if sp[0] == "con" and sp[3] in SHAPE_TEST:
            return seat
    return None


def fmt_hand(hand):
    return " ".join(f"{SUIT_SYM[s]}{x or '-'}" for s, x in
                    zip(SUITS, (hand.spades, hand.hearts, hand.diamonds, hand.clubs)))


def simulate(specs, n, max_tries, seed, n_samples=6,
             stop=lambda: False, progress=lambda a, t: None):
    """specs: seat -> ('fixed', handstr) | ('con', lo, hi, kind, mins) | ('random',)."""
    if seed not in (None, ""):
        random.seed(int(seed))

    predeal = {}
    for seat, sp in specs.items():
        if sp[0] == "fixed":
            predeal[seat] = H(sp[1])

    smart = _smart_seat(specs)
    if smart:
        _, lo, hi, kind, _ = specs[smart]
        predeal[smart] = SmartStack(SHAPE_TEST[kind], hcp, range(lo, hi + 1))

    rej = [(seat, sp) for seat, sp in specs.items()
           if sp[0] == "con" and seat != smart and _constrains(sp)]

    def accept(deal):
        for seat, sp in rej:
            _, lo, hi, kind, mins = sp
            hand = getattr(deal, ATTR[seat])
            if not (lo <= hand.hcp <= hi):
                return False
            if kind in SHAPE_TEST and not SHAPE_TEST[kind](hand):
                return False
            if kind == "minlen":
                sh = hand.shape
                if any(sh[i] < mins[i] for i in range(4)):
                    return False
        return True

    dealer = Deal.prepare(predeal)
    keys = [k for k, _, _ in GAMES] + [k for k, _, _ in SLAMS] + \
           ["any game", "any slam", "grand"]
    tally = {k: 0 for k in keys}
    samples, pending = [], []
    accepted = candidates = tries = 0

    def flush():
        nonlocal accepted
        if not pending:
            return
        for deal, t in zip(pending, solve_batch(pending)):
            g = s = False
            for lab, st, need in GAMES:
                if t[st] >= need:
                    tally[lab] += 1; g = True
            for lab, st, need in SLAMS:
                if t[st] >= need:
                    tally[lab] += 1; s = True
            tally["any game"] += g
            tally["any slam"] += s
            tally["grand"] += max(t.values()) >= 13
            if len(samples) < n_samples:
                samples.append(({seat: fmt_hand(getattr(deal, ATTR[seat]))
                                 for seat in ORDER}, dict(t)))
            accepted += 1
        pending.clear()

    while candidates < n:
        if stop():
            raise Aborted()
        if tries >= max_tries:
            break
        tries += 1
        deal = dealer()
        if rej and not accept(deal):
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
DEFAULTS = {
    "N": ("Constrain", "", "22", "24", "bal"),
    "E": ("Random", "", "0", "37", "any"),
    "S": ("Fixed", "9 J6 K9753 QJ654", "0", "37", "any"),
    "W": ("Random", "", "0", "37", "any"),
}


class App(tk.Frame):
    def __init__(self, master):
        super().__init__(master, padx=12, pady=10)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.q = queue.Queue()
        self.worker = None
        self.stop_flag = False
        self.mode, self.hand, self.hlo, self.hhi, self.shp = {}, {}, {}, {}, {}
        self._build()

    def _build(self):
        r = 0
        tk.Label(self, text="Fixed = '♠ ♥ ♦ ♣' e.g. 'AK5 QJT 9432 K8'"
                            "   |   Shape = bal / semibal / any / '0 5 4 0'",
                 font=("Segoe UI", 9)).grid(row=r, column=0, sticky="w"); r += 1

        sf = tk.Frame(self); sf.grid(row=r, column=0, sticky="we", pady=4); r += 1
        for c, h in enumerate(["Seat", "Mode", "Fixed hand", "HCP", "", "", "Shape"]):
            tk.Label(sf, text=h, font=("Segoe UI", 9, "bold")).grid(row=0, column=c, padx=2)
        for i, seat in enumerate(ORDER, start=1):
            m, hnd, lo, hi, shp = DEFAULTS[seat]
            tk.Label(sf, text=seat, font=("Segoe UI", 11, "bold")).grid(row=i, column=0)
            cb = ttk.Combobox(sf, width=9, state="readonly",
                              values=["Random", "Fixed", "Constrain"])
            cb.set(m); cb.grid(row=i, column=1, padx=2)
            cb.bind("<<ComboboxSelected>>", lambda _e, s=seat: self._sync(s))
            self.mode[seat] = cb
            e = tk.Entry(sf, width=20); e.insert(0, hnd)
            e.grid(row=i, column=2, padx=2); self.hand[seat] = e
            slo = tk.Spinbox(sf, from_=0, to=37, width=3); slo.delete(0); slo.insert(0, lo)
            slo.grid(row=i, column=3); self.hlo[seat] = slo
            tk.Label(sf, text="-").grid(row=i, column=4)
            shi = tk.Spinbox(sf, from_=0, to=37, width=3); shi.delete(0); shi.insert(0, hi)
            shi.grid(row=i, column=5); self.hhi[seat] = shi
            es = tk.Entry(sf, width=10); es.insert(0, shp)
            es.grid(row=i, column=6, padx=2); self.shp[seat] = es

        run = tk.Frame(self); run.grid(row=r, column=0, sticky="we", pady=(6, 2)); r += 1
        tk.Label(run, text="Deals").pack(side="left")
        self.ndeals = tk.Spinbox(run, from_=100, to=1000000, increment=1000, width=8)
        self.ndeals.delete(0); self.ndeals.insert(0, "5000"); self.ndeals.pack(side="left", padx=(2, 10))
        tk.Label(run, text="Seed").pack(side="left")
        self.seed = tk.Entry(run, width=7); self.seed.pack(side="left", padx=(2, 10))
        self.samples_var = tk.IntVar(value=1)
        tk.Checkbutton(run, text="samples", variable=self.samples_var).pack(side="left")
        self.run_btn = tk.Button(run, text="Run", width=8, command=self.run); self.run_btn.pack(side="left", padx=6)
        self.stop_btn = tk.Button(run, text="Stop", width=6, command=self.stop, state="disabled"); self.stop_btn.pack(side="left")
        tk.Button(run, text="Copy", width=6, command=self._copy).pack(side="left", padx=6)

        self.prog = tk.Label(self, text="", anchor="w", fg="#0a5")
        self.prog.grid(row=r, column=0, sticky="we"); r += 1
        self.out = tk.Text(self, width=60, height=22, font=("Consolas", 10), wrap="none")
        self.out.grid(row=r, column=0, sticky="nsew", pady=(6, 0))
        self.out.tag_config("warn", foreground="#c00")
        self.out.tag_config("head", font=("Consolas", 10, "bold"))
        self.rowconfigure(r, weight=1); self.columnconfigure(0, weight=1)
        for seat in ORDER:
            self._sync(seat)

    def _sync(self, seat):
        m = self.mode[seat].get()
        self.hand[seat].config(state="normal" if m == "Fixed" else "disabled")
        st = "normal" if m == "Constrain" else "disabled"
        for w in (self.hlo[seat], self.hhi[seat], self.shp[seat]):
            w.config(state=st)

    # ------------------------------------------------------------- execution
    def run(self):
        try:
            specs, fixed_cards = {}, {}
            for seat in ORDER:
                m = self.mode[seat].get()
                if m == "Fixed":
                    hstr, cards = parse_fixed(self.hand[seat].get())
                    for c in cards:
                        if c in fixed_cards:
                            raise ValueError(f"{seat}: {SUIT_SYM[c[0]]}{c[1]} "
                                             f"also in {fixed_cards[c]}")
                        fixed_cards[c] = seat
                    specs[seat] = ("fixed", hstr)
                elif m == "Constrain":
                    lo, hi = int(self.hlo[seat].get()), int(self.hhi[seat].get())
                    if lo > hi:
                        raise ValueError(f"{seat}: HCP min > max")
                    kind, mins = parse_shape(self.shp[seat].get())
                    specs[seat] = ("con", lo, hi, kind, mins)
                else:
                    specs[seat] = ("random",)
            n = int(self.ndeals.get())
        except ValueError as e:
            self.out.delete("1.0", "end"); self._log(f"!  {e}\n", "warn"); return

        smart = _smart_seat(specs)
        rej_present = any(sp[0] == "con" and seat != smart and _constrains(sp)
                          for seat, sp in specs.items())
        self.out.delete("1.0", "end")
        self.stop_flag = False
        self.run_btn.config(state="disabled"); self.stop_btn.config(state="normal")
        self.prog.config(text="preparing...")
        max_tries = max(n * 500, 2_000_000) if rej_present else n
        n_samples = 6 if self.samples_var.get() else 0
        args = (specs, n, max_tries, self.seed.get().strip(), n_samples)
        self.worker = threading.Thread(target=self._work, args=args, daemon=True)
        self.worker.start()
        self.after(80, self._poll)

    def stop(self):
        self.stop_flag = True

    def _work(self, specs, n, max_tries, seed, n_samples):
        try:
            res = simulate(specs, n, max_tries, seed, n_samples=n_samples,
                           stop=lambda: self.stop_flag,
                           progress=lambda a, t: self.q.put(("prog", a, t)))
            self.q.put(("done", res, n))
        except Aborted:
            self.q.put(("stopped",))
        except Exception as e:
            self.q.put(("error", repr(e)))

    def _poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                if m[0] == "prog":
                    _, a, t = m
                    rate = f"  ({100*a/t:.1f}% accepted)" if t else ""
                    self.prog.config(text=f"simulating... {a} deals / {t} tries{rate}")
                elif m[0] == "error":
                    self._log(f"Error: {m[1]}\n", "warn"); self._finish("error")
                elif m[0] == "stopped":
                    self._log("Stopped.\n", "warn"); self._finish("stopped")
                elif m[0] == "done":
                    self._report(*m[1], want=m[2]); self._finish("done")
        except queue.Empty:
            pass
        if self.worker and self.worker.is_alive():
            self.after(80, self._poll)

    def _finish(self, why):
        self.run_btn.config(state="normal"); self.stop_btn.config(state="disabled")
        if why == "done":
            self.prog.config(text="done.")

    def _report(self, tally, tries, accepted, samples, want):
        out = self.out
        if accepted == 0:
            self._log("No qualifying deals - the constraints may be impossible.\n", "warn")
            return
        n = accepted
        if accepted < want:
            self._log(f"!  Only {accepted} deals found in {tries} tries "
                      f"(rare constraint); results are for those.\n\n", "warn")

        def line(label, key):
            p = 100 * tally[key] / n
            se = 1.96 * (p * (100 - p) / n) ** 0.5
            out.insert("end", f"  {label:<11}{p:5.1f}% +-{se:3.1f}  {'#' * round(p / 5)}\n")

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
            out.insert("end", "\nSAMPLE DEALS\n", "head"); out.insert("end", "-" * 32 + "\n")
            for hands, t in samples:
                for seat in ORDER:
                    out.insert("end", f"  {seat} {hands[seat]}\n")
                out.insert("end", f"    tricks C{t['C']} D{t['D']} H{t['H']} "
                                  f"S{t['S']} NT{t['N']}\n")
        out.see("1.0")

    def _log(self, s, tag=None):
        self.out.insert("end", s, tag or ())
        self.out.see("end")

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.out.get("1.0", "end").rstrip())
        self.prog.config(text="results copied to clipboard.")


def main():
    root = tk.Tk()
    root.title("Bridge MC Simulator  -  Redeal + DDS")
    root.minsize(560, 620)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
