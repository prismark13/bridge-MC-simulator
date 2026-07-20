"""A persistent cache of exact suit-combination results.

The exact single-dummy solver is slow on the hardest two-honour holdings (up to
~2 minutes). But a suit combination only needs solving ONCE — the answer never
changes. This module stores results in a small SQLite table keyed by a canonical
form of the holding, so:

  * a holding solved once (even the slow ones) is instant forever after;
  * a precompute script can fill the table offline and ship it with the app
    (this is exactly what the ACBL Encyclopedia's suit-combination tables are).

Canonical key
-------------
The trick distribution depends only on the *pattern* of which hand (declarer-N,
declarer-S, or defenders-D) holds each rank, not on the absolute ranks — relabel
the low spots and the odds are unchanged. So the key is the run-length encoding
of the owner sequence from the ace down to the two, e.g. A-K with declarer and
everything else out is ``N2D11``. Declarer's two hands are interchangeable a
priori (the defenders sit symmetrically), so N and S are swapped to whichever
orientation sorts first. This is SOUND (it never merges holdings with different
odds); it is not maximal (a couple of genuinely-equal holdings can still get
separate keys), which only costs a little redundant storage, never correctness.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading

from .suitplay import parse_combo

_DB = os.path.join(os.path.dirname(__file__), "..", "data", "suits.db")
_local = threading.local()


def _owner_rle(hi, lo):
    """Run-length encode the owner sequence from rank 14 down to 2, with ``hi``
    the ranks of the first declarer hand and ``lo`` the second."""
    seq = []
    for r in range(14, 1, -1):
        o = "N" if r in hi else "S" if r in lo else "D"
        if seq and seq[-1][0] == o:
            seq[-1][1] += 1
        else:
            seq.append([o, 1])
    return "".join(f"{o}{c}" for o, c in seq)


def canon(top: str, bottom: str) -> str:
    N, S, _ = parse_combo(top, bottom)
    N, S = set(N), set(S)
    return min(_owner_rle(N, S), _owner_rle(S, N))


def _conn():
    c = getattr(_local, "conn", None)
    if c is None:
        os.makedirs(os.path.dirname(_DB), exist_ok=True)
        c = sqlite3.connect(_DB)
        c.execute("CREATE TABLE IF NOT EXISTS suits ("
                  "key TEXT PRIMARY KEY, cum TEXT NOT NULL, exact INTEGER NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS vec ("
                  "key TEXT PRIMARY KEY, data TEXT NOT NULL)")
        _local.conn = c
    return c


# --- full vec-prop results (odds + plans + line grid) -----------------------
# Same-canon holdings share the same cards (an N/S swap at most), so a stored
# plan — which names cards, not hands — is reusable across them. The display
# labels (which hand, the exact spots) are re-derived from the actual holding on
# lookup, so only the canonical-invariant payload lives in the DB.
_FULL_FIELDS = ("cum", "plans", "grid", "trees", "equiv", "mp", "max_tricks",
                "strategies", "worlds")


def get_full(top: str, bottom: str):
    try:
        row = _conn().execute("SELECT data FROM vec WHERE key=?",
                              (canon(top, bottom),)).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    d = json.loads(row[0])
    d["cum"] = {int(k): v for k, v in d.get("cum", {}).items()}
    d["plans"] = {int(k): v for k, v in d.get("plans", {}).items()}
    d["grid"] = {int(k): [tuple(x) for x in v] for k, v in d.get("grid", {}).items()}
    d["trees"] = {int(k): v for k, v in (d.get("trees") or {}).items()}
    d["equiv"] = {int(k): v for k, v in (d.get("equiv") or {}).items()}
    d["mp"] = d.get("mp")
    return d


def put_full(top: str, bottom: str, result: dict):
    keep = {k: result.get(k) for k in _FULL_FIELDS}
    keep["cum"] = {str(k): v for k, v in (keep["cum"] or {}).items()}
    keep["plans"] = {str(k): v for k, v in (keep["plans"] or {}).items()}
    keep["grid"] = {str(k): [list(x) for x in v]
                    for k, v in (keep["grid"] or {}).items()}
    keep["trees"] = {str(k): v for k, v in (keep["trees"] or {}).items()}
    keep["equiv"] = {str(k): v for k, v in (keep["equiv"] or {}).items()}
    try:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO vec(key, data) VALUES (?,?)",
                  (canon(top, bottom), json.dumps(keep)))
        c.commit()
    except sqlite3.Error:
        pass          # best-effort; a read-only fs just means no caching


def get(top: str, bottom: str):
    """Return {'cum': {tricks: pct}, 'exact': bool} for this holding, or None."""
    try:
        row = _conn().execute("SELECT cum, exact FROM suits WHERE key=?",
                              (canon(top, bottom),)).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    cum = {int(k): v for k, v in json.loads(row[0]).items()}
    return {"cum": cum, "exact": bool(row[1])}


def put(top: str, bottom: str, cum: dict, exact: bool):
    try:
        c = _conn()
        c.execute("INSERT OR REPLACE INTO suits(key, cum, exact) VALUES (?,?,?)",
                  (canon(top, bottom), json.dumps({str(k): v for k, v in cum.items()}),
                   1 if exact else 0))
        c.commit()
    except sqlite3.Error:
        pass          # cache is best-effort; a read-only fs just means no caching
