"""Precompute exact suit-combination odds into the cache DB (bridge_mc/data).

Enumerates realistic holdings by honour placement (A-K-Q-J-T-9 to declarer-N,
declarer-S, or the defenders) and hand lengths, filling declarer's remaining
slots with the lowest spots — the convention people use ("x" = a low card).
Canonicalises + dedups, then solves each exactly (a big per-holding budget, so
even the slow two-honour double-finesses get stored). Only exact results are
cached, so re-running with a larger budget upgrades any that fell back.

Run:  python -m scripts.precompute_suits [budget_seconds] [max_holdings]
It is resumable — already-cached holdings are skipped.
"""
import sys
import time
from itertools import product

from bridge_mc.domain.suitplay import VALRANK
from bridge_mc.domain import suitcache as SC
from bridge_mc.domain.suitplay_opt import suit_optimal

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
    budget = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    cap = int(sys.argv[2]) if len(sys.argv) > 2 else 10 ** 9
    max_def = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    done = exact = ceil = 0
    t0 = time.time()
    for top, bot in holdings(max_def):
        if SC.get(top, bot):                        # already cached (exact)
            continue
        if done >= cap:
            break
        st = time.time()
        r = suit_optimal(top, bot, time_budget=budget, use_cache=False)
        # suit_optimal only caches exact automatically; store nothing extra here
        done += 1
        if r.get("exact"):
            exact += 1
        else:
            ceil += 1
        print(f"[{done:>5}] {top or '-':7}/{bot or '-':7}  "
              f"{'EXACT' if r.get('exact') else 'ceiling':7} "
              f"maxT={r.get('max_tricks')} ({time.time()-st:.1f}s)", flush=True)
    print(f"\ndone={done} exact={exact} ceiling={ceil} in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
