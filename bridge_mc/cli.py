"""Headless entry point: run a simulation without any GUI.

Examples
--------
  python -m bridge_mc.cli --fixed S "Q9643 J AT86 KQ4" \\
      --con N "16-21:0,5,4,0" -n 20000 --seed 1
  python -m bridge_mc.cli --fixed S "..." --con N "22-24:bal" --html report.html
"""
import argparse
import sys

from .domain import ORDER, SimConfig, build_specs
from .engine import run
from .engine.sampling import smart_seat
from .report import render_html, render_text


def _con_to_fields(value):
    """'16-21:0,5,4,0' or '22-24:bal' -> (lo, hi, shape_text)."""
    rng, _, shape = value.partition(":")
    lo, _, hi = rng.partition("-")
    shape = shape.replace(",", " ").strip() or "any"
    return int(lo), int(hi or lo), shape


def build_config(args):
    raw = {seat: {"mode": "Random", "hand": "", "lo": 0, "hi": 37, "shape": "any"}
           for seat in ORDER}
    for seat, hand in (args.fixed or []):
        raw[seat] = {"mode": "Fixed", "hand": hand, "lo": 0, "hi": 37, "shape": "any"}
    for seat, spec in (args.con or []):
        lo, hi, shape = _con_to_fields(spec)
        raw[seat] = {"mode": "Constrain", "hand": "", "lo": lo, "hi": hi, "shape": shape}
    specs = build_specs(raw)
    smart = smart_seat(specs)
    rej = any(sp.kind == "con" and s != smart and sp.constrains
              for s, sp in specs.items())
    max_tries = max(args.deals * 500, 2_000_000) if rej else args.deals
    return SimConfig(specs=specs, n=args.deals, max_tries=max_tries,
                     seed=args.seed, side=args.side, vul=args.vul,
                     n_samples=args.samples)


def main(argv=None):
    p = argparse.ArgumentParser(prog="bridge-mc", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fixed", nargs=2, metavar=("SEAT", "HAND"), action="append",
                   help="fix a seat, e.g. --fixed S 'Q9643 J AT86 KQ4'")
    p.add_argument("--con", nargs=2, metavar=("SEAT", "SPEC"), action="append",
                   help="constrain a seat, e.g. --con N '16-21:0,5,4,0'")
    p.add_argument("-n", "--deals", type=int, default=5000)
    p.add_argument("--seed", default="")
    p.add_argument("--side", choices=["NS", "EW"], default="NS",
                   help="which side is 'us' (both sides are analysed)")
    p.add_argument("--vul", choices=["None", "NS", "EW", "Both"], default="None",
                   help="board vulnerability")
    p.add_argument("--samples", type=int, default=4)
    p.add_argument("--html", metavar="PATH", help="write an HTML report instead of text")
    args = p.parse_args(argv)

    try:
        config = build_config(args)
    except ValueError as e:
        p.error(str(e))

    result = run(config, progress=lambda a, t: print(
        f"\r{a}/{config.n} deals ({t} tries)", end="", file=sys.stderr))
    print("", file=sys.stderr)

    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(render_html(result))
        print(f"wrote {args.html}")
    else:
        _print_utf8(render_text(result))


def _print_utf8(s):
    """Print unicode (suit glyphs, bars) without dying on a legacy cp1252 console."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # real streams, py3.7+
    except Exception:
        pass
    try:
        print(s)
    except UnicodeEncodeError:
        buf = getattr(sys.stdout, "buffer", None)
        if buf is not None:
            buf.write((s + "\n").encode("utf-8")); buf.flush()
        else:
            enc = sys.stdout.encoding or "utf-8"
            print(s.encode(enc, "replace").decode(enc))


if __name__ == "__main__":
    main()
