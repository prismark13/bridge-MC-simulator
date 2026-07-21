"""FastAPI app: a mobile-friendly form that runs the simulation and returns the
same styled HTML report you get in the desktop app.

Endpoints
---------
GET  /         the input form (hands, options, auction, ask)
GET  /suit     the suit-combination calculator (its own screen)
POST /suit/solve  solve one suit combination, return the result fragment
POST /run      run the engine, return render_html(result)
POST /explain  stream Claude's verdict for the last-submitted parameters
GET  /healthz  liveness probe for the cloud platform

Everything is gated behind HTTP basic auth **iff** APP_PASS is set in the
environment (always set it in the cloud). The simulation itself needs no API
key; only /explain needs ANTHROPIC_API_KEY, held server-side.
"""
from __future__ import annotations

import asyncio
import os
import secrets
from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from bridge_mc.ai import HAVE_ANTHROPIC, build_prompt, stream_explanation
from bridge_mc.domain import ORDER, VUL_LABEL, VUL_STATES, SimConfig, build_specs
from bridge_mc.engine import run
from bridge_mc.engine.sampling import smart_seat
from bridge_mc.report import render_html

# --- guards -----------------------------------------------------------------
MAX_DEALS = int(os.environ.get("MAX_DEALS", "8000"))   # cap CPU per request
RUN_TIMEOUT = int(os.environ.get("RUN_TIMEOUT", "120"))  # seconds
_pool = ThreadPoolExecutor(max_workers=2)              # keep one core free

app = FastAPI(title="Bridge MC Simulator")
_security = HTTPBasic(auto_error=True)


def auth(creds: HTTPBasicCredentials = Depends(_security)):
    """Enforce basic auth only when APP_PASS is configured (prod)."""
    want_user = os.environ.get("APP_USER", "bridge")
    want_pass = os.environ.get("APP_PASS", "")
    if not want_pass:                                  # local/dev: open
        return
    ok = (secrets.compare_digest(creds.username, want_user)
          and secrets.compare_digest(creds.password, want_pass))
    if not ok:
        raise HTTPException(status_code=401, detail="bad credentials",
                            headers={"WWW-Authenticate": "Basic"})


# When APP_PASS is unset the dependency still runs but returns immediately; to
# avoid forcing a login prompt in dev we swap it out for a no-op there.
_guard = auth if os.environ.get("APP_PASS") else (lambda: None)


@app.get("/suit", response_class=HTMLResponse)
def suit_page(_=Depends(_guard)):
    """The suit calculator gets its own screen: a different question, and a
    different input — tapping cards beats typing holdings on a phone."""
    from .suit import page
    return page()


@app.post("/suit/solve", response_class=HTMLResponse)
async def suit_solve(request: Request, _=Depends(_guard)):
    from .suit import solve_html
    f = await _form_dict(request)
    top, bottom = (f.get("top") or "").strip(), (f.get("bottom") or "").strip()

    def _ent(key):
        v = (f.get(key) or "").strip()
        return int(v) if v.isdigit() else None
    eN, eS, start = _ent("eN"), _ent("eS"), (f.get("start") or "F")
    entries = None if eN is None and eS is None else (eN if eN is not None else 99,
                                                      eS if eS is not None else 99)
    loop = asyncio.get_running_loop()
    try:                       # keep the event loop free; cap CPU per request
        return await asyncio.wait_for(
            loop.run_in_executor(_pool, solve_html, top, bottom, 20.0,
                                 entries, start), timeout=40)
    except asyncio.TimeoutError:
        return ("<p class='warn'>Timed out — this is one of the slow holdings. "
                "Try the desktop app.</p>")


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/", response_class=HTMLResponse)
def form(_=Depends(_guard)):
    return FORM_HTML


def _raw_from_form(f: dict) -> dict:
    return {seat: {
        "mode": f.get(f"{seat}_mode", "Random"),
        "hand": f.get(f"{seat}_hand", "").strip(),
        "lo": int(f.get(f"{seat}_lo", 0) or 0),
        "hi": int(f.get(f"{seat}_hi", 37) or 37),
        "shape": f.get(f"{seat}_shape", "any").strip() or "any",
        "honors": f.get(f"{seat}_honors", "").strip(),
    } for seat in ORDER}


def _config_from_form(f: dict) -> SimConfig:
    specs = build_specs(_raw_from_form(f))
    n = max(100, min(MAX_DEALS, int(f.get("deals", 2000) or 2000)))
    smart = smart_seat(specs)
    rej = any(sp.kind == "con" and ((s != smart and sp.constrains) or sp.has_honors)
              for s, sp in specs.items())
    max_tries = max(n * 500, 2_000_000) if rej else n
    return SimConfig(
        specs=specs, n=n, max_tries=max_tries, seed=(f.get("seed", "") or "").strip(),
        side=f.get("side", "NS"), vul=f.get("vul", "None"),
        n_samples=6 if f.get("samples") else 0,
        finesse=bool(f.get("finesse")),
        dealer=f.get("dealer", "N"), auction=(f.get("auction", "") or "").strip())


async def _form_dict(request: Request) -> dict:
    return dict(await request.form())


@app.post("/run", response_class=HTMLResponse)
async def run_sim(request: Request, _=Depends(_guard)):
    f = await _form_dict(request)
    try:
        config = _config_from_form(f)
    except (ValueError, KeyError) as e:
        return HTMLResponse(_error_page(str(e)), status_code=400)
    loop = asyncio.get_event_loop()
    try:
        fut = loop.run_in_executor(_pool, run, config)
        result = await asyncio.wait_for(fut, timeout=RUN_TIMEOUT)
    except asyncio.TimeoutError:
        return HTMLResponse(_error_page(
            f"Timed out after {RUN_TIMEOUT}s — try fewer deals or turn off confidence."),
            status_code=504)
    except Exception as e:                              # noqa: BLE001 - surface to user
        return HTMLResponse(_error_page(f"{type(e).__name__}: {e}"), status_code=500)
    return HTMLResponse(render_html(result, theme=f.get("theme", "light")))


@app.post("/explain")
async def explain(request: Request, _=Depends(_guard)):
    if not (HAVE_ANTHROPIC and os.environ.get("ANTHROPIC_API_KEY")):
        return PlainTextResponse("Explain unavailable — no ANTHROPIC_API_KEY on the "
                                 "server.", status_code=503)
    f = await _form_dict(request)
    try:
        config = _config_from_form(f)
    except (ValueError, KeyError) as e:
        return PlainTextResponse(str(e), status_code=400)
    result = run(config)
    prompt = build_prompt(result, (f.get("ask", "") or "").strip())

    def gen():
        try:
            yield from stream_explanation(prompt)
        except Exception as e:                          # noqa: BLE001
            yield f"\n[AI error: {e}]"
    return StreamingResponse(gen(), media_type="text/plain")


def _error_page(msg: str) -> str:
    return (f'<!doctype html><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<body style="font-family:system-ui;max-width:40rem;margin:3rem auto;'
            f'padding:0 1rem;color:#333"><h2>Could not run</h2>'
            f'<p style="color:#b00">{msg}</p><p><a href="/">&larr; back</a></p></body>')


# --- the form ---------------------------------------------------------------
def _seat_rows() -> str:
    rows = []
    defaults = {
        "N": ("Constrain", "", 18, 19, "semibal"),
        "E": ("Random", "", 0, 37, "any"),
        "S": ("Fixed", "AJ972 K976 AJT 2", 0, 37, "any"),
        "W": ("Random", "", 0, 37, "any"),
    }
    for seat in ORDER:
        m, hand, lo, hi, shp = defaults[seat]
        opts = "".join(f'<option{" selected" if o == m else ""}>{o}</option>'
                       for o in ("Random", "Fixed", "Constrain"))
        rows.append(f"""
      <fieldset class="seat">
        <legend>{seat}</legend>
        <label>Mode<select name="{seat}_mode">{opts}</select></label>
        <label class="wide">Fixed hand
          <input name="{seat}_hand" value="{hand}" placeholder="AK5 QJT 9432 K8"></label>
        <label>HCP
          <span class="hcp"><input type="number" name="{seat}_lo" value="{lo}" min="0" max="37">
          &ndash;<input type="number" name="{seat}_hi" value="{hi}" min="0" max="37"></span></label>
        <label>Shape<input name="{seat}_shape" value="{shp}" placeholder="bal / 3-5 5+ 0-4 x"></label>
        <label>Honours<input name="{seat}_honors" placeholder="DAK H2/3 ctrl3+"></label>
      </fieldset>""")
    return "".join(rows)


def _vul_opts() -> str:
    return "".join(f'<option value="{v}"{" selected" if v == "None" else ""}>'
                   f'{VUL_LABEL[v]}</option>' for v in VUL_STATES)


FORM_HTML = f"""<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bridge MC Simulator</title>
<style>
  :root{{--bg:#f3f2ef;--card:#fff;--ink:#1b1b18;--muted:#78776f;--line:#e0dfd9;
    --accent:#2c7a50;--pip:#9a3b3b}}
  @media(prefers-color-scheme:dark){{:root{{--bg:#141412;--card:#1c1b18;--ink:#ecebe5;
    --muted:#8e8d84;--line:#2c2b26;--accent:#5ec48d}}}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.5 system-ui,"Segoe UI",Roboto,sans-serif;padding:16px}}
  .wrap{{max-width:720px;margin:0 auto}}
  nav{{display:flex;gap:8px;margin:0 0 14px}}
  nav a{{flex:1;text-align:center;padding:8px;border:1px solid var(--line);
    border-radius:8px;text-decoration:none;color:var(--muted);font-size:14px}}
  nav a.on{{background:var(--accent);color:#fff;border-color:var(--accent);font-weight:600}}
  h1{{font-size:20px;margin:0 0 4px}} .sub{{color:var(--muted);margin:0 0 16px;font-size:13px}}
  form{{display:flex;flex-direction:column;gap:14px}}
  .seat{{border:1px solid var(--line);border-radius:10px;background:var(--card);
    padding:10px 12px;display:grid;grid-template-columns:1fr 1fr;gap:8px 12px;margin:0}}
  .seat legend{{font-weight:700;padding:0 6px}}
  label{{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--muted)}}
  label.wide{{grid-column:1/-1}}
  input,select{{font:inherit;font-size:15px;color:var(--ink);background:var(--bg);
    border:1px solid var(--line);border-radius:8px;padding:8px;width:100%}}
  .hcp{{display:flex;align-items:center;gap:6px}} .hcp input{{width:5rem}}
  .opts{{border:1px solid var(--line);border-radius:10px;background:var(--card);
    padding:10px 12px;display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}}
  .check{{flex-direction:row;align-items:center;gap:8px}} .check input{{width:auto}}
  .full{{grid-column:1/-1}}
  button{{font:inherit;font-weight:600;font-size:16px;padding:12px;border:0;border-radius:10px;
    background:var(--accent);color:#fff;cursor:pointer}}
  button.ghost{{background:transparent;color:var(--accent);border:1px solid var(--accent)}}
  .row{{display:flex;gap:10px}} .row>*{{flex:1}}
  #out{{margin-top:16px}} iframe{{width:100%;height:78vh;border:1px solid var(--line);border-radius:10px}}
  pre{{white-space:pre-wrap;background:var(--card);border:1px solid var(--line);
    border-radius:10px;padding:12px;font-size:13px}}
  .busy{{color:var(--accent);font-size:13px;min-height:1.2em}}
  details summary{{cursor:pointer;color:var(--muted);font-size:13px}}
</style></head>
<body><div class="wrap">
  <nav><a href="/" class="on">Simulator</a><a href="/suit">Suit play</a></nav>
  <h1>Bridge MC Simulator</h1>
  <p class="sub">Fix a hand, constrain partner, run thousands of double-dummy deals.
     Suit contracts are scored from the realistic (long-trump) declarer.</p>
  <form id="f">
    {_seat_rows()}
    <div class="opts">
      <label>Us<select name="side"><option>NS</option><option>EW</option></select></label>
      <label>Vul<select name="vul">{_vul_opts()}</select></label>
      <label>Deals<input type="number" name="deals" value="500" min="100" max="{MAX_DEALS}" step="250"></label>
      <label>Seed<input name="seed" placeholder="optional"></label>
      <label class="check"><input type="checkbox" name="samples" checked>samples</label>
      <label class="check"><input type="checkbox" name="finesse">confidence (~2× slower)</label>
      <label class="full">Auction — dealer
        <span class="hcp">
          <select name="dealer" style="width:5rem"><option>N</option><option>E</option><option>S</option><option>W</option></select>
          <input name="auction" placeholder="1D P 1H P 4H P P P — blank = best declarer">
        </span></label>
      <label class="full">Ask Claude<input name="ask" placeholder="blank = standard bid/stop verdict"></label>
    </div>
    <div class="row">
      <button type="submit">Run</button>
      <button type="button" class="ghost" id="explain">Explain</button>
    </div>
    <div class="busy" id="busy"></div>
  </form>
  <div id="out"></div>
</div>
<script>
  const f = document.getElementById('f'), out = document.getElementById('out'),
        busy = document.getElementById('busy');
  f.addEventListener('submit', async (e) => {{
    e.preventDefault(); busy.textContent = 'simulating…'; out.innerHTML = '';
    const r = await fetch('/run', {{method:'POST', body:new FormData(f)}});
    const html = await r.text(); busy.textContent = '';
    const frame = document.createElement('iframe'); out.appendChild(frame);
    frame.srcdoc = html; frame.scrollIntoView({{behavior:'smooth'}});
  }});
  document.getElementById('explain').addEventListener('click', async () => {{
    busy.textContent = 'asking Claude…';
    const pre = document.createElement('pre'); out.innerHTML=''; out.appendChild(pre);
    const r = await fetch('/explain', {{method:'POST', body:new FormData(f)}});
    if (!r.ok) {{ pre.textContent = await r.text(); busy.textContent=''; return; }}
    const reader = r.body.getReader(), dec = new TextDecoder();
    for(;;){{ const {{done,value}} = await reader.read(); if(done) break;
      pre.textContent += dec.decode(value, {{stream:true}}); }}
    busy.textContent = '';
  }});
</script>
</body></html>"""
