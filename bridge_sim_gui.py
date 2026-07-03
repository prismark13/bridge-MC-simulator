"""
Bridge slam/game Monte-Carlo GUI.
You (South) hold a fixed hand; partner (North) is constrained by HCP + shape.
Deals are generated with Redeal (smartstack when possible) and solved
double-dummy with the bundled DDS engine. Shows make-rates for the key spots.
"""
import queue
import threading
import tkinter as tk
from tkinter import ttk

from redeal import Deal, H, SmartStack, balanced, semibalanced, hcp

SUITS = ["S", "H", "D", "C"]
SUIT_SYM = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}
STRAINS = ["C", "D", "H", "S", "N"]          # N = notrump


def best_ns(deal, strain):
    """Max double-dummy tricks for the NS partnership in a strain."""
    return max(deal.dd_tricks(f"1{strain}N"), deal.dd_tricks(f"1{strain}S"))


class App(tk.Frame):
    def __init__(self, master):
        super().__init__(master, padx=12, pady=10)
        self.pack(fill="both", expand=True)
        self.q = queue.Queue()
        self.worker = None
        self.stop_flag = False
        self._build()

    # ---------------------------------------------------------------- layout
    def _build(self):
        r = 0
        tk.Label(self, text="YOUR HAND (South)", font=("Segoe UI", 10, "bold")
                 ).grid(row=r, column=0, columnspan=8, sticky="w"); r += 1
        self.south = {}
        default = {"S": "9", "H": "J6", "D": "K9753", "C": "QJ654"}
        c = 0
        for s in SUITS:
            tk.Label(self, text=SUIT_SYM[s], font=("Segoe UI", 12)).grid(row=r, column=c)
            e = tk.Entry(self, width=10); e.insert(0, default[s])
            e.grid(row=r, column=c + 1, padx=(0, 10)); self.south[s] = e; c += 2
        r += 1

        tk.Label(self, text="PARTNER (North)", font=("Segoe UI", 10, "bold")
                 ).grid(row=r, column=0, columnspan=8, sticky="w", pady=(10, 0)); r += 1

        tk.Label(self, text="HCP").grid(row=r, column=0, sticky="e")
        self.hcp_lo = tk.Spinbox(self, from_=0, to=37, width=4); self.hcp_lo.delete(0); self.hcp_lo.insert(0, "22")
        self.hcp_lo.grid(row=r, column=1, sticky="w")
        tk.Label(self, text="to").grid(row=r, column=2)
        self.hcp_hi = tk.Spinbox(self, from_=0, to=37, width=4); self.hcp_hi.delete(0); self.hcp_hi.insert(0, "24")
        self.hcp_hi.grid(row=r, column=3, sticky="w"); r += 1

        tk.Label(self, text="Shape").grid(row=r, column=0, sticky="e")
        self.shape = ttk.Combobox(self, values=[
            "Balanced (fast)", "Semibalanced (fast)", "Custom min-lengths (filter)"],
            width=24, state="readonly")
        self.shape.set("Balanced (fast)")
        self.shape.grid(row=r, column=1, columnspan=3, sticky="w"); r += 1

        tk.Label(self, text="Min lengths  ♠♥♦♣").grid(row=r, column=0, sticky="e")
        self.mins = tk.Entry(self, width=10); self.mins.insert(0, "0 5 4 0")
        self.mins.grid(row=r, column=1, columnspan=3, sticky="w")
        tk.Label(self, text="(used only in Custom mode)").grid(row=r, column=4, columnspan=3, sticky="w"); r += 1

        tk.Label(self, text="Deals").grid(row=r, column=0, sticky="e")
        self.ndeals = tk.Spinbox(self, from_=100, to=1000000, increment=1000, width=8)
        self.ndeals.delete(0); self.ndeals.insert(0, "10000")
        self.ndeals.grid(row=r, column=1, sticky="w")
        self.run_btn = tk.Button(self, text="Run", width=8, command=self.run)
        self.run_btn.grid(row=r, column=2)
        self.stop_btn = tk.Button(self, text="Stop", width=8, command=self.stop, state="disabled")
        self.stop_btn.grid(row=r, column=3); r += 1

        self.prog = tk.Label(self, text="", anchor="w")
        self.prog.grid(row=r, column=0, columnspan=8, sticky="we"); r += 1

        self.out = tk.Text(self, width=60, height=18, font=("Consolas", 10))
        self.out.grid(row=r, column=0, columnspan=8, sticky="we", pady=(6, 0))

    # ------------------------------------------------------------- execution
    def run(self):
        try:
            south = {s: self.south[s].get().replace(" ", "") or "-" for s in SUITS}
            south_hand = H(" ".join(south[s] for s in SUITS))
            lo, hi = int(self.hcp_lo.get()), int(self.hcp_hi.get())
            n = int(self.ndeals.get())
            mode = self.shape.get()
            mins = [int(x) for x in self.mins.get().split()] if "Custom" in mode else [0, 0, 0, 0]
        except Exception as e:
            self._log(f"Input error: {e}\n"); return
        self.out.delete("1.0", "end")
        self.stop_flag = False
        self.run_btn.config(state="disabled"); self.stop_btn.config(state="normal")
        self.worker = threading.Thread(
            target=self._simulate, args=(south_hand, lo, hi, n, mode, mins), daemon=True)
        self.worker.start()
        self.after(100, self._poll)

    def stop(self):
        self.stop_flag = True

    def _simulate(self, south_hand, lo, hi, n, mode, mins):
        try:
            predeal = {"S": south_hand}
            if "Balanced" in mode:
                predeal["N"] = SmartStack(balanced, hcp, range(lo, hi + 1)); acc = None
            elif "Semibalanced" in mode:
                predeal["N"] = SmartStack(semibalanced, hcp, range(lo, hi + 1)); acc = None
            else:
                def acc(deal):
                    if not (lo <= deal.north.hcp <= hi):
                        return False
                    sh = deal.north.shape
                    return all(sh[i] >= mins[i] for i in range(4))
            dealer = Deal.prepare(predeal)
            tally = {k: 0 for k in ("3NT", "5C", "5D", "6C", "6D", "6NT", "6m", "slam")}
            done = 0
            for _ in range(n):
                if self.stop_flag:
                    break
                deal = dealer(acc) if acc else dealer()
                C, D, Hh, S, NT = (best_ns(deal, x) for x in STRAINS)
                tally["3NT"] += NT >= 9
                tally["5C"] += C >= 11
                tally["5D"] += D >= 11
                tally["6C"] += C >= 12
                tally["6D"] += D >= 12
                tally["6NT"] += NT >= 12
                tally["6m"] += max(C, D) >= 12
                tally["slam"] += max(C, D, Hh, S, NT) >= 12
                done += 1
                if done % 200 == 0:
                    self.q.put(("prog", done, n))
            self.q.put(("done", tally, done))
        except Exception as e:
            self.q.put(("error", repr(e)))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "prog":
                    _, done, n = msg
                    self.prog.config(text=f"simulating… {done}/{n}")
                elif msg[0] == "error":
                    self._log(f"Error: {msg[1]}\n"); self._finish()
                elif msg[0] == "done":
                    self._report(msg[1], msg[2]); self._finish()
        except queue.Empty:
            pass
        if self.worker and self.worker.is_alive():
            self.after(100, self._poll)

    def _finish(self):
        self.run_btn.config(state="normal"); self.stop_btn.config(state="disabled")
        self.prog.config(text="done.")

    def _report(self, t, n):
        if n == 0:
            self._log("No deals generated.\n"); return

        def pc(k):
            p = 100 * t[k] / n
            se = 1.96 * (p * (100 - p) / n) ** 0.5
            return f"{p:5.1f}%  ±{se:.1f}"
        L = self._log
        L(f"{n} deals (double-dummy, best NS declarer)\n")
        L("-" * 40 + "\n")
        for label, key in [("3NT", "3NT"), ("5♣", "5C"), ("5♦", "5D"),
                           ("6♣", "6C"), ("6♦", "6D"), ("6NT", "6NT"),
                           ("6 best-minor", "6m"), ("any slam", "slam")]:
            L(f"  {label:<14} {pc(key)}\n")

    def _log(self, s):
        self.out.insert("end", s); self.out.see("end")


def main():
    root = tk.Tk()
    root.title("Bridge Slam Simulator  —  Redeal + DDS")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
