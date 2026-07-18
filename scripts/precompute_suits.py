"""Precompute exact suit-combination results into the cache DB (bridge_mc/data).

Enumerates realistic holdings by honour placement (A-K-Q-J-T-9 to declarer-N,
declarer-S, or the defenders) and hand lengths, filling declarer's remaining
slots with the lowest spots — the convention people use ("x" = a low card).
Canonicalises + dedups, then solves each with the vec-prop solver (exact on
every holding) and stores the full result (odds + plans + line grid). A big
per-holding budget means even the slow spot-heavy holdings complete and cache.

Run:  python -m scripts.precompute_suits [budget_seconds] [max_holdings]
It is resumable — already-cached holdings are skipped.
"""
import sys
import time
from itertools import product

from bridge_mc.domain.suitplay import VALRANK
from bridge_mc.domain import suitcache as SC
from bridge_mc.domain.suitplay_vec import suit_vec, Timeout

HONS = [14, 13, 12, 11, 10, 9]          # A K Q J T 9
LOWS = [8, 7, 6, 5, 4, 3, 2]            # spot cards


def holdings(max_def=6):
    """Yield (top, bottom) representatives, deduped by canonical form.
    ``max_def`` caps defenders' length (13 - declarer length)."""
    seen = set()
    for lenN in range(1, 12):
        for lenS in range(1, lenN + 1):
            if not (13 - max_def <= lenN + lenS <= 11):   # realistic lengths
                continue
            for hp in product("NSD", repeat=len(HONS)):
                hn = [HONS[i] for i in range(len(HONS)) if hp[i] == "N"]
                hs = [HONS[i] for i in range(len(HONS)) if hp[i] == "S"]
                need_n, need_s = lenN - len(hn), lenS - len(hs)
                if need_n < 0 or need_s < 0 or need_n + need_s > len(LOWS):
                    continue
                nlow = LOWS[:need_n]
                slow = LOWS[need_n:need_n + need_s]
                top = "".join(VALRANK[r] for r in hn + nlow)
                bot = "".join(VALRANK[r] for r in hs + slow)
                k = SC.canon(top, bot)
                if k in seen:
                    continue
                seen.add(k)
                yield top, bot


def main():
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
    cap = int(sys.argv[2]) if len(sys.argv) > 2 else 10 ** 9
    max_def = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    done = timed = slow = 0
    t0 = time.time()
    for top, bot in holdings(max_def):
        if SC.get_full(top, bot):                   # already cached
            continue
        if done >= cap:
            break
        st = time.time()
        try:
            r = suit_vec(top, bot, time_budget=budget)   # caches on success
        except Timeout:
            timed += 1
            print(f"[   -- ] {top or '-':8}/{bot or '-':8}  TIMEOUT "
                  f"({time.time()-st:.0f}s)", flush=True)
            continue
        dt = time.time() - st
        done += 1
        if dt > 3:
            slow += 1
        print(f"[{done:>5}] {top or '-':8}/{bot or '-':8}  maxT={r.get('max_tricks')} "
              f"strat={r.get('strategies')} ({dt:.1f}s)", flush=True)
    print(f"\ncached={done} timed-out={timed} slow(>3s)={slow} "
          f"in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
