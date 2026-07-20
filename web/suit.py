"""The suit-combination calculator as its own phone screen.

Separate from the simulator form: it asks a different question ("how do I play
this suit?") and wants a different input — tapping cards into two hands rather
than typing holdings, which is painful on a phone.

Answers come from the vec-prop solver (exact, SuitPlay-validated), and are shown
per trick target: what you need, the chance, and the play that gets it. The best
line for the maximum is often a shot you would never take, so it never implies a
single answer.
"""
from __future__ import annotations

RANKS = list("AKQJT98765432")


def _render_tree(node) -> str:
    """The plan tree as nested HTML: the main line flows top to bottom; each
    'if the king appears' exception is a drillable <details> you can expand."""
    if not node:
        return ""
    html, cur = "", node
    while cur:
        html += f"<div class='step'>{cur['action']}</div>"
        for nt in cur.get("notes") or []:
            html += (f"<details class='ex'><summary>{nt['cond']}</summary>"
                     f"<div class='exbody'>{_render_tree(nt['node'])}</div>"
                     f"</details>")
        cur = cur.get("next")
    return html


def solve_html(top: str, bottom: str, budget: float = 20.0) -> str:
    from bridge_mc.domain.suitplay_vec import suit_vec, Timeout
    if not (top or bottom):
        return ("<p class='warn'>Tap some cards into a hand first — leave the "
                "other empty for a void opposite.</p>")
    try:
        r = suit_vec(top, bottom, time_budget=budget)
    except Timeout:
        return ("<p class='warn'>This holding is one of the slow ones and timed "
                "out. Try it on the desktop app, which allows longer.</p>")
    except Exception as e:                                   # noqa: BLE001
        return f"<p class='warn'>{e}</p>"

    cum, plans = r["cum"], r.get("plans") or {}
    trees = r.get("trees") or {}
    if not cum:
        return "<p class='warn'>Nothing to analyse.</p>"
    rows = ""
    for k in sorted(cum, reverse=True):
        pct = cum[k]
        plan = plans.get(k) or ("<span class='dim'>any line</span>"
                                if pct >= 99.95 else "<span class='dim'>—</span>")
        tree = trees.get(k)
        drill = (f"<details class='drill'><summary>line</summary>"
                 f"<div class='tree'>{_render_tree(tree)}</div></details>"
                 if tree else "")
        rows += (f"<tr><td class='t'>{k}</td>"
                 f"<td class='p'>{pct:.1f}<span class='pc'>%</span></td>"
                 f"<td class='pl'>{plan}{drill}</td></tr>")
    # Matchpoints — maximise the average tricks (a different question).
    mp = r.get("mp")
    mp_block = ""
    if mp and mp.get("tree"):
        eq = mp.get("equiv") or []
        eqnote = (f"<div class='eqnote'>equivalent: {' = '.join(eq)}</div>"
                  if len(eq) > 1 else "")
        drill = (f"<details class='drill'><summary>line</summary>"
                 f"<div class='tree'>{_render_tree(mp['tree'])}</div></details>")
        mp_block = (f"<div class='sec'>matchpoints — play for the average</div>"
                    f"<table class='need'><tr>"
                    f"<td class='t'>&asymp;{mp['tricks']:.2f}</td>"
                    f"<td class='pl' colspan='2'>{mp.get('plan','')}"
                    f"{eqnote}{drill}</td></tr></table>")

    grid = r.get("grid") or {}
    alts = ""
    for k in sorted(grid, reverse=True):
        top_p = grid[k][0][0] if grid[k] else 0
        parts = []
        for i, (p, d) in enumerate(grid[k]):
            tied = i > 0 and abs(p - top_p) < 0.05
            mark = "<span class='eq'>=</span> " if tied else ""
            parts.append(f"{mark}{d} <b>{p:.1f}%</b>")
        alts += (f"<tr><td class='t'>{k}</td>"
                 f"<td class='pl'>{' · '.join(parts)}</td></tr>")
    alt_block = ""
    if alts:
        alt_block = (f"<div class='sec'>best lines by target "
                     f"<span class='dim'>(= equally good)</span></div>"
                     f"<table class='alts'>{alts}</table>")
    return (f"<div class='res'>"
            f"<div class='holding'>{top or '<i>void</i>'}"
            f"<span class='vs'>opposite</span>{bottom or '<i>void</i>'}</div>"
            f"<div class='dim sml'>defenders hold <b>{r['missing']}</b> · "
            f"exact — best line vs best defence</div>"
            f"<div class='sec'>if you need</div>"
            f"<table class='need'>"
            f"<tr><th>tricks</th><th>chance</th><th>best play</th></tr>{rows}</table>"
            f"{mp_block}{alt_block}</div>")


def _rank_row(hand: str) -> str:
    return "".join(
        f"<button type='button' class='c' data-h='{hand}' data-r='{r}'>"
        f"{'10' if r == 'T' else r}</button>" for r in RANKS)


SUIT_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Suit calculator</title>
<style>
  :root{--bg:#f3f2ef;--card:#fff;--ink:#1b1b18;--muted:#78776f;--line:#e0dfd9;
    --accent:#2c7a50;--h1:#2f5fa8;--h2:#2c7a50}
  @media(prefers-color-scheme:dark){:root{--bg:#141412;--card:#1c1b18;--ink:#ecebe5;
    --muted:#8e8d84;--line:#2c2b26;--accent:#5ec48d;--h1:#5b8cd6;--h2:#5ec48d}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.5 system-ui,"Segoe UI",Roboto,sans-serif;padding:14px}
  .wrap{max-width:720px;margin:0 auto}
  nav{display:flex;gap:8px;margin:0 0 14px}
  nav a{flex:1;text-align:center;padding:8px;border:1px solid var(--line);
    border-radius:8px;text-decoration:none;color:var(--muted);font-size:14px}
  nav a.on{background:var(--accent);color:#fff;border-color:var(--accent);font-weight:600}
  h1{font-size:19px;margin:0 0 3px} .sub{color:var(--muted);margin:0 0 14px;font-size:13px}
  .pick{border:1px solid var(--line);border-radius:10px;background:var(--card);
    padding:10px;margin-bottom:10px}
  .lab{font-size:12px;font-weight:700;margin-bottom:6px}
  .lab.h1{color:var(--h1)} .lab.h2{color:var(--h2)}
  .row{display:grid;grid-template-columns:repeat(13,1fr);gap:3px}
  .c{font:inherit;font-size:13px;font-weight:600;padding:9px 0;border:1px solid var(--line);
    border-radius:5px;background:var(--bg);color:var(--ink);cursor:pointer;min-height:38px}
  .c.on1{background:var(--h1);border-color:var(--h1);color:#fff}
  .c.on2{background:var(--h2);border-color:var(--h2);color:#fff}
  .c.taken{opacity:.32}
  .prev{font-family:ui-monospace,Consolas,monospace;font-size:14px;margin:8px 0 0;
    color:var(--muted)}
  .prev b{color:var(--ink)}
  .btns{display:flex;gap:8px;margin:12px 0}
  button.go{flex:2;font:inherit;font-weight:700;font-size:16px;padding:12px;border:0;
    border-radius:10px;background:var(--accent);color:#fff}
  button.clr{flex:1;font:inherit;padding:12px;border:1px solid var(--line);
    border-radius:10px;background:transparent;color:var(--muted)}
  .busy{color:var(--accent);font-size:13px;min-height:1.2em}
  .res{border:1px solid var(--line);border-radius:10px;background:var(--card);padding:12px}
  .holding{font-family:ui-monospace,Consolas,monospace;font-size:19px;font-weight:700}
  .vs{font-family:system-ui;font-size:12px;font-weight:400;color:var(--muted);margin:0 8px}
  .dim{color:var(--muted)} .sml{font-size:12px;margin-top:4px}
  .sec{font-size:10px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);
    margin:14px 0 5px}
  table{border-collapse:collapse;width:100%}
  th{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
    text-align:left;padding:0 8px 5px 0;font-weight:600}
  td{padding:6px 8px 6px 0;vertical-align:baseline;font-size:13px}
  tr+tr td{border-top:1px solid var(--line)}
  .need .t{font-weight:700;width:3.2em}
  .need .p{font-weight:700;font-variant-numeric:tabular-nums;width:4.4em}
  .pc{font-size:10px;color:var(--muted);font-weight:400}
  .need .pl{color:var(--ink)}
  .alts .t{font-weight:700;width:3.2em;color:var(--muted)}
  .alts .pl{color:var(--muted);font-size:12px}
  .eq{color:var(--accent);font-weight:800}
  .eqnote{color:var(--muted);font-style:italic;font-size:12px;margin-top:3px}
  .drill{margin-top:5px}
  .drill>summary{color:var(--accent);font-size:12px;cursor:pointer;
    list-style:none;display:inline-block;padding:2px 8px;border:1px solid var(--line);
    border-radius:6px}
  .drill>summary::-webkit-details-marker{display:none}
  .drill[open]>summary{margin-bottom:6px}
  .tree{border-left:2px solid var(--line);padding-left:10px;margin-left:2px}
  .step{font-size:13px;padding:2px 0}
  .ex{margin:3px 0 3px 4px}
  .ex>summary{color:var(--muted);font-size:12px;cursor:pointer;font-style:italic}
  .exbody{border-left:2px solid var(--line);padding-left:10px;margin:3px 0 5px 4px}
  .warn{color:#b0243a;font-size:14px}
</style></head>
<body><div class="wrap">
  <nav><a href="/">Simulator</a><a href="/suit" class="on">Suit play</a></nav>
  <h1>Suit calculator</h1>
  <p class="sub">Tap cards into each hand — everything you don't tap is the
     defenders'. Exact odds and the real line, per trick target.</p>

  <div class="pick">
    <div class="lab h1">Hand 1</div>
    <div class="row" id="r1">__ROW1__</div>
  </div>
  <div class="pick">
    <div class="lab h2">Hand 2</div>
    <div class="row" id="r2">__ROW2__</div>
    <div class="prev" id="prev"></div>
  </div>

  <div class="btns">
    <button class="go" id="go">Solve</button>
    <button class="clr" id="clr">Clear</button>
  </div>
  <div class="busy" id="busy"></div>
  <div id="out"></div>
</div>
<script>
const RANKS = "AKQJT98765432".split("");
const state = {};                       // rank -> "1" | "2"
const prev = document.getElementById('prev');

function paint(){
  document.querySelectorAll('.c').forEach(b => {
    const r = b.dataset.r, h = b.dataset.h, s = state[r];
    b.classList.toggle('on1', s === '1' && h === '1');
    b.classList.toggle('on2', s === '2' && h === '2');
    b.classList.toggle('taken', !!s && s !== h);
  });
  const h1 = RANKS.filter(r => state[r] === '1').join('');
  const h2 = RANKS.filter(r => state[r] === '2').join('');
  const op = RANKS.filter(r => !state[r]).join('');
  prev.innerHTML = `Hand 1 <b>${h1 || 'void'}</b> &nbsp; Hand 2 <b>${h2 || 'void'}</b>`
                 + ` &nbsp; defenders ${op || '—'}`;
  return [h1, h2];
}
document.querySelectorAll('.c').forEach(b => b.addEventListener('click', () => {
  const r = b.dataset.r, h = b.dataset.h;
  state[r] = (state[r] === h) ? undefined : h;
  if (state[r] === undefined) delete state[r];
  paint();
}));
document.getElementById('clr').addEventListener('click', () => {
  for (const k of Object.keys(state)) delete state[k];
  paint(); document.getElementById('out').innerHTML = '';
});
document.getElementById('go').addEventListener('click', async () => {
  const [h1, h2] = paint();
  const busy = document.getElementById('busy'), out = document.getElementById('out');
  busy.textContent = 'solving…'; out.innerHTML = '';
  const fd = new FormData(); fd.append('top', h1); fd.append('bottom', h2);
  const r = await fetch('/suit/solve', {method:'POST', body:fd});
  out.innerHTML = await r.text(); busy.textContent = '';
  out.scrollIntoView({behavior:'smooth', block:'nearest'});
});
paint();
</script>
</body></html>"""


def page() -> str:
    return (SUIT_HTML.replace("__ROW1__", _rank_row("1"))
                     .replace("__ROW2__", _rank_row("2")))
