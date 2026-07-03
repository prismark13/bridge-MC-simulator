"""
Bridge game/slam Monte-Carlo GUI.

Each seat (N/E/S/W) can be Random, a Fixed hand, or Constrained (HCP + shape).
Deals are generated with Redeal (smartstack when one constrained seat is
balanced) and solved double-dummy with the bundled DDS engine.  Reports how
often every game and slam makes for the chosen side, with 95% CIs, plus average
score and the expected IMP gain of bidding the slam over the best game.
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
from tkinter import ttk, font as tkfont

from redeal import Deal, H, SmartStack, balanced, semibalanced, hcp
from redeal import dds as _rdds
try:
    from redeal import Contract
except ImportError:
    from redeal.redeal import Contract

try:
    import sv_ttk
    _HAVE_SV = True
except Exception:
    _HAVE_SV = False

# ---------------------------------------------------------------------------
# Fast double-dummy via DDS's batched CalcAllTables (32 tables/call, all cores)
# ---------------------------------------------------------------------------
BATCH = 32
_SI = {"S": 0, "H": 1, "D": 2, "C": 3, "N": 4}


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
    """DD-solve up to BATCH deals; per deal -> {strain: (tN, tE, tS, tW)}."""
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
        out.append({s: (rt[_SI[s]][0], rt[_SI[s]][1], rt[_SI[s]][2], rt[_SI[s]][3])
                    for s in STRAINS})
    return out


# ---------------------------------------------------------------------------
# Simulation core
# ---------------------------------------------------------------------------
SUITS = ["S", "H", "D", "C"]
SUIT_SYM = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
RANKS = "AKQJT98765432"
STRAINS = ["C", "D", "H", "S", "N"]
ORDER = ["N", "E", "S", "W"]
ATTR = {"N": "north", "E": "east", "S": "south", "W": "west"}
SHAPE_TEST = {"bal": balanced, "semibal": semibalanced}
SIDE_IDX = {"NS": (0, 2), "EW": (1, 3)}

#            label  strain  need  contract-string
GAMES = [("3NT", "N", 9, "3N"), ("4H", "H", 10, "4H"), ("4S", "S", 10, "4S"),
         ("5C", "C", 11, "5C"), ("5D", "D", 11, "5D")]
SLAMS = [("6C", "C", 12, "6C"), ("6D", "D", 12, "6D"), ("6H", "H", 12, "6H"),
         ("6S", "S", 12, "6S"), ("6NT", "N", 12, "6N")]
ALL_CS = [g[3] for g in GAMES] + [s[3] for s in SLAMS]

# IMP table: upper bound of point-difference for each IMP value 0..24
_IMP_UP = [10, 40, 80, 120, 160, 210, 260, 310, 360, 420, 490, 590, 740, 890,
           1090, 1290, 1490, 1740, 1990, 2240, 2490, 2990, 3490, 3990]
_STORE_CAP = 100_000     # cap for keeping per-deal scores (IMP swing)


def to_imps(diff):
    a = abs(diff)
    n = 0
    for u in _IMP_UP:
        if a > u:
            n += 1
        else:
            break
    return n if diff >= 0 else -n


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
    for seat in ORDER:
        sp = specs[seat]
        if sp[0] == "con" and sp[3] in SHAPE_TEST:
            return seat
    return None


def fmt_hand(hand):
    return " ".join(f"{SUIT_SYM[s]}{x or '-'}" for s, x in
                    zip(SUITS, (hand.spades, hand.hearts, hand.diamonds, hand.clubs)))


def simulate(specs, n, max_tries, seed, side="NS", vul=False, n_samples=6,
             stop=lambda: False, progress=lambda a, t: None):
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
    score_lut = {cs: [Contract.from_str(cs, vul=vul).score(t) for t in range(14)]
                 for cs in ALL_CS}
    i0, i1 = SIDE_IDX[side]

    labels = [g[0] for g in GAMES] + [s[0] for s in SLAMS] + \
             ["any game", "any slam", "grand"]
    make = {k: 0 for k in labels}
    score = {g[0]: 0 for g in GAMES}
    score.update({s[0]: 0 for s in SLAMS})
    gvecs, svecs = [], []
    store = n <= _STORE_CAP
    samples, pending = [], []
    accepted = candidates = tries = 0

    def flush():
        nonlocal accepted
        if not pending:
            return
        for deal, tv in zip(pending, solve_batch(pending)):
            st = {s: max(v[i0], v[i1]) for s, v in tv.items()}
            g = s = False
            gvec = []
            for lab, strain, need, cs in GAMES:
                sc = score_lut[cs][st[strain]]
                score[lab] += sc; gvec.append(sc)
                if st[strain] >= need:
                    make[lab] += 1; g = True
            svec = []
            for lab, strain, need, cs in SLAMS:
                sc = score_lut[cs][st[strain]]
                score[lab] += sc; svec.append(sc)
                if st[strain] >= need:
                    make[lab] += 1; s = True
            make["any game"] += g
            make["any slam"] += s
            make["grand"] += max(st.values()) >= 13
            if store:
                gvecs.append(gvec); svecs.append(svec)
            if len(samples) < n_samples:
                samples.append(({seat: fmt_hand(getattr(deal, ATTR[seat]))
                                 for seat in ORDER}, dict(st)))
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

    if accepted == 0:
        return {"n": 0, "tries": tries, "side": side, "vul": vul}

    gEV = [score[g[0]] / accepted for g in GAMES]
    sEV = [score[s[0]] / accepted for s in SLAMS]
    bg = max(range(len(GAMES)), key=lambda i: gEV[i])
    bs = max(range(len(SLAMS)), key=lambda i: sEV[i])
    imp = None
    if store and gvecs:
        imp = sum(to_imps(svecs[k][bs] - gvecs[k][bg])
                  for k in range(len(gvecs))) / len(gvecs)
    return {
        "n": accepted, "tries": tries, "side": side, "vul": vul,
        "make": make, "score": score, "samples": samples,
        "best_game": (GAMES[bg][0], gEV[bg]),
        "best_slam": (SLAMS[bs][0], sEV[bs]),
        "ev_diff": sEV[bs] - gEV[bg], "imp": imp,
    }


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
DEFAULTS = {
    "N": ("Constrain", "", "22", "24", "bal"),
    "E": ("Random", "", "0", "37", "any"),
    "S": ("Fixed", "9 J6 K9753 QJ654", "0", "37", "any"),
    "W": ("Random", "", "0", "37", "any"),
}
THEME_TEXT = {"light": ("#ffffff", "#1a1a1a"), "dark": ("#1c1c1c", "#e6e6e6")}


class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=14)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.q = queue.Queue()
        self.worker = None
        self.stop_flag = False
        self.mode, self.hand, self.hlo, self.hhi, self.shp = {}, {}, {}, {}, {}
        self._build()

    def _build(self):
        self.columnconfigure(0, weight=1)
        head = ttk.Frame(self); head.grid(row=0, column=0, sticky="we")
        ttk.Label(head, text="Bridge MC Simulator",
                  font=("Segoe UI Semibold", 16)).pack(side="left")
        self.theme_btn = ttk.Button(head, text="◐ Theme", width=9,
                                    command=self._toggle_theme)
        self.theme_btn.pack(side="right")

        # --- Hands ---------------------------------------------------------
        hands = ttk.Labelframe(self, text="Hands", padding=10)
        hands.grid(row=1, column=0, sticky="we", pady=(10, 6))
        ttk.Label(hands, foreground="#888",
                  text="Fixed: '♠ ♥ ♦ ♣' e.g. AK5 QJT 9432 K8    "
                       "Shape: bal / semibal / any / '0 5 4 0'"
                  ).grid(row=0, column=0, columnspan=7, sticky="w", pady=(0, 6))
        for c, h in enumerate(["", "Mode", "Fixed hand", "HCP", "", "", "Shape"]):
            ttk.Label(hands, text=h, font=("Segoe UI", 9, "bold")).grid(
                row=1, column=c, padx=3, sticky="w")
        for i, seat in enumerate(ORDER, start=2):
            m, hnd, lo, hi, shp = DEFAULTS[seat]
            ttk.Label(hands, text=seat, font=("Segoe UI Semibold", 11)).grid(row=i, column=0, padx=(0, 4))
            cb = ttk.Combobox(hands, width=10, state="readonly",
                              values=["Random", "Fixed", "Constrain"])
            cb.set(m); cb.grid(row=i, column=1, padx=3, pady=2)
            cb.bind("<<ComboboxSelected>>", lambda _e, s=seat: self._sync(s))
            self.mode[seat] = cb
            e = ttk.Entry(hands, width=22); e.insert(0, hnd)
            e.grid(row=i, column=2, padx=3); self.hand[seat] = e
            slo = ttk.Spinbox(hands, from_=0, to=37, width=4); slo.set(lo)
            slo.grid(row=i, column=3); self.hlo[seat] = slo
            ttk.Label(hands, text="–").grid(row=i, column=4)
            shi = ttk.Spinbox(hands, from_=0, to=37, width=4); shi.set(hi)
            shi.grid(row=i, column=5); self.hhi[seat] = shi
            es = ttk.Entry(hands, width=11); es.insert(0, shp)
            es.grid(row=i, column=6, padx=3); self.shp[seat] = es

        # --- Options -------------------------------------------------------
        opt = ttk.Labelframe(self, text="Options", padding=10)
        opt.grid(row=2, column=0, sticky="we", pady=6)
        ttk.Label(opt, text="Analyse").grid(row=0, column=0, padx=(0, 3))
        self.side = ttk.Combobox(opt, width=5, state="readonly", values=["NS", "EW"])
        self.side.set("NS"); self.side.grid(row=0, column=1, padx=(0, 12))
        ttk.Label(opt, text="Vul").grid(row=0, column=2, padx=(0, 3))
        self.vul = ttk.Combobox(opt, width=9, state="readonly", values=["Non-vul", "Vul"])
        self.vul.set("Non-vul"); self.vul.grid(row=0, column=3, padx=(0, 12))
        ttk.Label(opt, text="Deals").grid(row=0, column=4, padx=(0, 3))
        self.ndeals = ttk.Spinbox(opt, from_=100, to=1000000, increment=1000, width=8)
        self.ndeals.set("5000"); self.ndeals.grid(row=0, column=5, padx=(0, 12))
        ttk.Label(opt, text="Seed").grid(row=0, column=6, padx=(0, 3))
        self.seed = ttk.Entry(opt, width=7); self.seed.grid(row=0, column=7, padx=(0, 12))
        self.samples_var = tk.IntVar(value=1)
        ttk.Checkbutton(opt, text="samples", variable=self.samples_var).grid(row=0, column=8)

        # --- Actions -------------------------------------------------------
        act = ttk.Frame(self); act.grid(row=3, column=0, sticky="we", pady=(2, 4))
        self.run_btn = ttk.Button(act, text="Run", command=self.run, style="Accent.TButton")
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(act, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(act, text="Copy results", command=self._copy).pack(side="left")
        self.prog = ttk.Label(act, text="", foreground="#0a7")
        self.prog.pack(side="left", padx=12)

        # --- Results -------------------------------------------------------
        self.out = tk.Text(self, width=62, height=24, relief="flat",
                           font=("Consolas", 10), wrap="none", borderwidth=8)
        self.out.grid(row=4, column=0, sticky="nsew", pady=(6, 0))
        self.out.tag_config("warn", foreground="#d33")
        self.out.tag_config("head", font=("Consolas", 10, "bold"))
        self.out.tag_config("good", foreground="#0a7")
        self.rowconfigure(4, weight=1)

        self._apply_theme(_HAVE_SV and sv_ttk.get_theme() or "light")
        for seat in ORDER:
            self._sync(seat)

    # ------------------------------------------------------------- theming
    def _apply_theme(self, name):
        bg, fg = THEME_TEXT.get(name, THEME_TEXT["light"])
        self.out.config(background=bg, foreground=fg, insertbackground=fg)

    def _toggle_theme(self):
        if not _HAVE_SV:
            return
        sv_ttk.toggle_theme()
        self._apply_theme(sv_ttk.get_theme())

    def _sync(self, seat):
        m = self.mode[seat].get()
        self.hand[seat].config(state="normal" if m == "Fixed" else "disabled")
        st = "normal" if m == "Constrain" else "disabled"
        for w in (self.hlo[seat], self.hhi[seat], self.shp[seat]):
            w.config(state=st)

    # ------------------------------------------------------------ execution
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
            self.out.delete("1.0", "end"); self._log(f"⚠  {e}\n", "warn"); return

        smart = _smart_seat(specs)
        rej_present = any(sp[0] == "con" and seat != smart and _constrains(sp)
                          for seat, sp in specs.items())
        self.out.delete("1.0", "end")
        self.stop_flag = False
        self.run_btn.config(state="disabled"); self.stop_btn.config(state="normal")
        self.prog.config(text="preparing…")
        max_tries = max(n * 500, 2_000_000) if rej_present else n
        n_samples = 6 if self.samples_var.get() else 0
        side = self.side.get()
        vul = self.vul.get() == "Vul"
        args = (specs, n, max_tries, self.seed.get().strip(), side, vul, n_samples)
        self.worker = threading.Thread(target=self._work, args=args, daemon=True)
        self.worker.start()
        self.after(80, self._poll)

    def stop(self):
        self.stop_flag = True

    def _work(self, specs, n, max_tries, seed, side, vul, n_samples):
        try:
            res = simulate(specs, n, max_tries, seed, side=side, vul=vul,
                           n_samples=n_samples, stop=lambda: self.stop_flag,
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
                    self.prog.config(text=f"simulating…  {a} deals / {t} tries{rate}")
                elif m[0] == "error":
                    self._log(f"Error: {m[1]}\n", "warn"); self._finish("error")
                elif m[0] == "stopped":
                    self._log("Stopped.\n", "warn"); self._finish("stopped")
                elif m[0] == "done":
                    self._report(m[1], m[2]); self._finish("done")
        except queue.Empty:
            pass
        if self.worker and self.worker.is_alive():
            self.after(80, self._poll)

    def _finish(self, why):
        self.run_btn.config(state="normal"); self.stop_btn.config(state="disabled")
        if why == "done":
            self.prog.config(text="done.")

    def _report(self, res, want):
        out = self.out
        if res["n"] == 0:
            self._log("No qualifying deals — the constraints may be impossible.\n", "warn")
            return
        n, tries = res["n"], res["tries"]
        make, score = res["make"], res["score"]
        if n < want:
            self._log(f"⚠  Only {n} deals in {tries} tries (rare constraint).\n\n", "warn")

        def row(label, key, scored):
            p = 100 * make[key] / n
            se = 1.96 * (p * (100 - p) / n) ** 0.5
            av = f"{score[key] / n:+6.0f}" if scored else "      "
            out.insert("end", f"  {label:<10}{p:5.1f}% ±{se:3.1f}   {av}   {'▉' * round(p / 5)}\n")

        acc = 100 * n / tries if tries else 0
        out.insert("end", f"{n} deals   ({tries} tries, {acc:.1f}% accepted)\n")
        out.insert("end", f"double-dummy · analysing {res['side']} · "
                          f"{'vulnerable' if res['vul'] else 'non-vul'}\n")
        out.insert("end", f"\n{'':<10}{'make':^10}{'avg score':^9}\n", "head")
        out.insert("end", "GAMES\n", "head")
        for lab, _, _, _ in GAMES:
            row(lab, lab, True)
        row("any game", "any game", False)
        out.insert("end", "SLAMS\n", "head")
        for lab, _, _, _ in SLAMS:
            row(lab, lab, True)
        row("any slam", "any slam", False)
        row("grand(7)", "grand", False)

        bg_l, bg_ev = res["best_game"]
        bs_l, bs_ev = res["best_slam"]
        out.insert("end", "\nBIDDING DECISION\n", "head")
        out.insert("end", f"  best game : {bg_l:<4} EV {bg_ev:+.0f}\n")
        out.insert("end", f"  best slam : {bs_l:<4} EV {bs_ev:+.0f}\n")
        tag = "good" if res["ev_diff"] > 0 else "warn"
        imp = f",  {res['imp']:+.2f} IMP/board" if res["imp"] is not None else ""
        out.insert("end", f"  slam vs game: {res['ev_diff']:+.0f} pts{imp}\n", tag)
        verdict = "bid the slam" if res["ev_diff"] > 0 else "stay in game"
        out.insert("end", f"  → {verdict}\n", tag)

        if res["samples"]:
            out.insert("end", "\nSAMPLE DEALS\n", "head")
            for hands, st in res["samples"]:
                for seat in ORDER:
                    out.insert("end", f"  {seat} {hands[seat]}\n")
                out.insert("end", f"    tricks  C{st['C']} D{st['D']} H{st['H']} "
                                  f"S{st['S']} NT{st['N']}\n")
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
    root.title("Bridge MC Simulator")
    if _HAVE_SV:
        sv_ttk.set_theme("light")
    else:
        try:
            ttk.Style().theme_use("clam")
        except tk.TclError:
            pass
    root.minsize(600, 680)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
