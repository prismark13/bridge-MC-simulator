# Bridge MC Simulator

A small Monte-Carlo tool for **bridge bidding analysis**. You fix your own hand,
constrain partner by HCP and shape, and it generates thousands of deals and
solves each one **double-dummy** to report how often games and slams make — and
**which partner hands should bid on**.

It ships with a PySide6 (Qt) desktop GUI, a headless CLI, and a couple of
example scripts.

![screenshot placeholder](docs/screenshot.png)

## What it answers

For a fixed hand opposite a constrained partner, e.g. *"partner is 5+♥ 4+♦,
16–21"*, it reports make-rates for **every game and slam** with 95% confidence
intervals, a bidding-decision readout, and a breakdown of **when the decision
contract makes** by partner's strength and shape:

```
1500 deals   (1500 tries, 100.0% accepted)
double-dummy · analysing NS · non-vul

GAMES
  3NT      98.3% ±0.6    +440  ▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉
  5D       91.4% ±1.4    +374  ▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉▉
SLAMS
  6D       54.1% ±2.5    +472  ▉▉▉▉▉▉▉▉▉▉▉

BIDDING DECISION
  best game : 3NT  EV +440
  best slam : 6D   EV +472
  slam vs game: +33 pts,  +0.75 IMP/board   → bid the slam

WHEN 6D MAKES  (by N's hand)
  by HCP    16 HCP 38%   17 HCP 51%   18 HCP 74%   20 HCP 91%   21 HCP 98%
  by trumps 4♦ 51%   5♦ 70%   6+♦ 78%
  by shape  singleton/void 57%   doubleton 45%
```

So you can decide not just *whether* the slam is worth it, but which partner
hands should accept a slam try and which should sign off.

**Features**

- **Per-seat input:** every seat (N/E/S/W) can be Random, a Fixed hand, or
  Constrained (HCP + shape + honours). A **card picker** builds fixed hands
  click-by-click (with a live preview) and blocks any card already used in
  another hand. Shapes take per-suit ranges: `5`/`5+` (minimum), `3-5` (range),
  `0-2` (maximum), `x` (any) — e.g. `3-5 5+ 0-4 x`.
- **Honour constraints** per seat: specific holdings (`DAK`, `HQxx`, `Sxx`),
  N-of-top-M (`H2/3` = 2 of the top 3 hearts), and controls (`ctrl3+`).
- **Both sides, every run:** make-rates for you *and* the opponents at game,
  slam, and **grand-slam** level, with average score and the **expected IMP
  gain** of bidding the slam. The verdict compares game vs small slam vs grand
  by expected value — so it recommends the **grand** when 7 makes nearly as
  often as 6, not just "bid the slam".
- **Full board vulnerability** (None / N-S / E-W / Both): each side is scored at
  its own vulnerability.
- **Adaptive report:** the run is classified as a slam / game / **competitive**
  deal and shows the pane that fits — slam-vs-game and a *which-hands-bid-on*
  breakdown on constructive deals; on a competitive deal it drops the slam
  content and shows the sides side-by-side instead.
- **Realistic declarer:** contracts are scored from the hand that would actually
  play them — a **suit from the long-trump hand**, notrump from the better hand —
  instead of an optimistic best-of-both. On hands where the long suit sits
  opposite the tenaces this matters a lot (the lead has to come *up* to the
  honours), so a slam can be 20–30% worse played from the wrong side.
- **Auction-aware declarer** (optional): type the **auction** (dealer + calls,
  e.g. `1D P 1H P 4H P P P`) and the report names the seat the auction installs
  as declarer, scores the final contract from *that* hand, and flags the
  **wrong-side cost** when the good contract is stuck in the weak hand.
- **Card-placement / finesse split** (optional): re-solves each deal with the
  defenders swapped to show how much of a contract is **position-proof** (makes
  however the cards lie) vs **hinging on card placement** (a finesse/endplay you
  must read) — useful for choosing between two contracts of equal DD make-rate.
- **Competitive tools:** DDS **par** (optimal result with doubled sacrifices),
  and a **compete-vs-pass EV** panel — the average equity of bidding vs passing,
  with the opponents doubling or bidding on optimally. It adapts to the deal:
  a **sacrifice** over their game, or a **partscore competition / overcall** when
  they're only in a partscore.
- **Styled report inside the app:** rendered by QtWebEngine (Chromium), with a
  **Log** tab for the raw text. **Save…** to `.html`/`.txt`, or **Open in
  browser** for a full-window view. Light/dark toggle.
- **Ask Claude:** type a question ("should North move with 18+ or good
  distribution?") and the optional **🧠 Explain** answers it from the simulation
  numbers; blank gives the standard bid/stop verdict. A **🧠 auto** option runs
  it when each simulation finishes. Needs `ANTHROPIC_API_KEY` (the simulator
  itself needs no network or key); easiest is to paste your key into `apikey.txt`
  (git-ignored) and run **`run_with_ai.bat`** on Windows.
- Input validation, optional RNG **seed**, sample deals, and a **Stop** button.
- Fast: batched `CalcAllTables` across all cores solves ~32 full deals per DDS
  call, and smart importance-sampling means no wasted deals (see below).

## How it works

- **Deal generation:** [Redeal](https://github.com/anntzer/redeal) with
  **SmartStack** importance sampling. A constrained seat — balanced,
  semi-balanced, *or* minimum-length like `5+♥ 4+♦` — is generated **directly**
  rather than by slow rejection, so even rare partner types run at ~100%
  acceptance. Any *second* constrained seat is handled by rejection (with a
  try-cap so impossible constraints can't hang).
- **Evaluation:** Bo Haglund & Søren Hein's **DDS** double-dummy solver
  (bundled with Redeal), the same engine behind most serious bridge software.

## Project layout

```
bridge_mc/
  domain/   pure data + rules (no redeal, no Qt)
  engine/   DDS solver, deal sampling, the simulation loop
  report/   HTML + text renderers
  ai/       Claude "explain" prompt + stream
  app/      PySide6 GUI
  cli.py    headless entry point
bridge_sim_gui.py   launcher shim (kept for the packaged build)
```

The domain and engine carry no UI dependency, so the simulation is usable (and
testable) with no display.

## Install

Requires **Python 3.9+**.

```sh
python -m pip install PySide6 anthropic
python -m pip install "git+https://github.com/anntzer/redeal"   # bundles DDS
```

On Windows, if `git` isn't handy, download Redeal's main-branch ZIP and
`python -m pip install redeal-main.zip`. The `examples/` scripts instead use
[endplay](https://pypi.org/project/endplay/) (`python -m pip install endplay`).

## Usage

**GUI:**

```sh
python bridge_sim_gui.py        # or:  python -m bridge_mc
```

Each seat (N/E/S/W) has a **Mode**:

- **Random** — dealt at random.
- **Fixed** — the exact 13 cards as `♠ ♥ ♦ ♣`, e.g. `AK5 QJT 9432 K8` (`-` void).
- **Constrain** — an **HCP** range, a **Shape**, and optional **Honours**:
  - Shape: `bal` / `semibal`, `any`, or four per-suit lengths `♠ ♥ ♦ ♣` where
    each is a min (`5`/`5+`), a range (`3-5`), a max (`0-2`), or any (`x`) —
    e.g. `0 5 4 0` = 5+♥ 4+♦, or `3-5 5+ 0-4 x`.
  - Honours (space-separated): holdings `DAK` / `HQxx` / `Sxx`, N-of-top-M
    `H2/3` (2 of the top 3 hearts), and controls `ctrl3+` / `ctrl3-5`.

One constrained seat is importance-sampled (any of the shapes above); pick a deal
count and hit **Run**.

**Headless CLI** (no Qt):

```sh
python -m bridge_mc.cli --fixed S "Q9643 J AT86 KQ4" --con N "16-21:0,5,4,0" -n 20000
python -m bridge_mc.cli --fixed S "..." --con N "22-24:bal" --html report.html
# name the declarer via an auction (who plays it changes the make-rate):
python -m bridge_mc.cli --fixed N "AJ8 2 A9732 AKT5" --fixed S "Q65 AQJT84 KQ 63" \
    --dealer N --auction "1D P 1H P 3H P 6H P P P"
```

**Example scripts:**

```sh
python examples/sim_partner_5h4d.py     # 6D vs 3NT, partner 5+H/4+D 16-21
python examples/sim_slam_finesse.py     # minor-suit slam + finesse-dependence test
```

## Standalone app (no Python needed)

Grab the packaged build from the
[Releases](https://github.com/prismark13/bridge-MC-simulator/releases) page — a
zipped one-folder app (`BridgeMCSimulator/BridgeMCSimulator.exe` on Windows) that
bundles Python, Qt, Redeal and the DDS solver, so end users install nothing.

Build it yourself:

```powershell
# Windows  ->  dist\BridgeMCSimulator\BridgeMCSimulator.exe
powershell -ExecutionPolicy Bypass -File build.ps1
```

QtWebEngine ships a Chromium helper process, so the app is built one-folder
(`--onedir`), not `--onefile`. A GitHub Actions workflow
(`.github/workflows/build.yml`) builds and publishes all three platforms when a
`v*` tag is pushed.

## Run from your phone (web / cloud)

There's a headless **web front-end** (`web/app.py`, FastAPI) that reuses the same
engine and report with **no Qt** — so you drive it from any phone browser. Same
hands / options / **auction** / Ask-Claude fields; it returns the identical
styled report.

**Try it locally** (phone on the same Wi-Fi hits `http://<your-pc-ip>:8080`):

```sh
python -m pip install -r requirements-web.txt      # fastapi, uvicorn, redeal…
python -m uvicorn web.app:app --host 0.0.0.0 --port 8080
```

**Deploy it always-on** (reachable anywhere, no PC needed). A `Dockerfile` +
`fly.toml` are included; [Fly.io](https://fly.io) is the easy path (any
Docker host works — Render, Railway, a VPS):

```sh
# one-time
fly launch --copy-config --no-deploy        # edit `app` in fly.toml to a unique name
fly secrets set APP_PASS=pick-a-password     # gates the whole app behind a login
fly secrets set ANTHROPIC_API_KEY=sk-ant-…   # optional — only for the Explain button
fly deploy
```

Then open `https://<your-app>.fly.dev` on your phone and log in. Notes:

- **`APP_PASS` is required in the cloud** — without it the app is open to anyone
  (and it runs CPU + can spend your Claude credits). With it set, every page is
  behind HTTP basic auth; `/healthz` stays open for the platform probe.
- Guards: `MAX_DEALS` caps deals per request and `RUN_TIMEOUT` abandons a run
  that overruns — both are env vars (defaults 8000 / 120s).
- By default the machine **scales to zero** when idle (pennies) and wakes in
  ~2–3s on the next request; set `min_machines_running = 1` in `fly.toml` for
  zero cold-start.
- The simulation needs **no API key**; only Explain does.

## A word on double-dummy numbers

Double-dummy solving assumes **perfect play by everyone with all 52 cards
visible** — so it always guesses two-way finesses right and finds every squeeze.
Real single-dummy results run roughly ½–1 trick worse. `sim_slam_finesse.py`
includes an East/West-swap test that estimates how much of a contract's success
depends on card *position* (a finesse/guess) versus power and breaks.

## License

GPL-3.0 — because this depends on Redeal, which is GPL-3.0. The bundled DDS
solver has its own permissive license (see the Redeal / DDS projects).
