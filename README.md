# Bridge Slam Simulator

A small Monte-Carlo tool for **bridge bidding analysis**. You fix your own hand,
constrain partner by HCP and shape, and it generates thousands of deals and
solves each one **double-dummy** to report how often games and slams make.

It ships with a Tk GUI and a couple of headless example scripts.

![screenshot placeholder](docs/screenshot.png)

## What it answers

For a fixed hand opposite a constrained partner, e.g. *"partner is 22–24
balanced"*, it reports make-rates like:

```
10000 deals (double-dummy, best NS declarer)
----------------------------------------
  3NT            94.4%  ±0.4
  5♦             90.8%  ±0.6
  6 best-minor   68.2%  ±0.9
  any slam       69.5%  ±0.9
```

...with 95% confidence intervals, so you can decide whether a slam try is worth
it and which strain is the right spot.

## How it works

- **Deal generation:** [Redeal](https://github.com/anntzer/redeal) — with
  **smartstack** importance sampling, so rare partner types (like 22–24
  balanced) are generated directly instead of by slow rejection.
- **Evaluation:** Bo Haglund & Søren Hein's **DDS** double-dummy solver
  (bundled with Redeal), the same engine behind most serious bridge software.

## Install

Requires **Python 3.8+**.

Redeal is easiest to install straight from its repo (its DDS binaries ride
along):

```sh
# macOS / Linux (needs git + libgomp)
python -m pip install "git+https://github.com/anntzer/redeal"

# Windows: download the main-branch ZIP from the Redeal repo and
python -m pip install redeal-main.zip
```

The example scripts under `examples/` instead use
[endplay](https://pypi.org/project/endplay/) (a pip-installable all-in-one that
also bundles DDS):

```sh
python -m pip install endplay
```

## Usage

**GUI:**

```sh
python bridge_sim_gui.py
```

- Enter your hand (South) in the four suit boxes.
- Set partner's HCP range and shape:
  - *Balanced / Semibalanced (fast)* → uses smartstack.
  - *Custom min-lengths (filter)* → type min length per suit `♠♥♦♣`
    (e.g. `0 5 4 0` = 5+♥, 4+♦) → uses rejection sampling.
- Pick a deal count and hit **Run**.

**Headless examples:**

```sh
python examples/sim_partner_5h4d.py     # 6D vs 3NT, partner 5+H/4+D 16-21
python examples/sim_slam_finesse.py     # minor-suit slam + finesse-dependence test
```

## A word on double-dummy numbers

Double-dummy solving assumes **perfect play by everyone with all 52 cards
visible** — so it always guesses two-way finesses right and finds every squeeze.
Real single-dummy results run roughly ½–1 trick worse. `sim_slam_finesse.py`
includes an East/West-swap test that estimates how much of a contract's success
depends on card *position* (a finesse/guess) versus power and breaks.

## License

GPL-3.0 — because this depends on Redeal, which is GPL-3.0. The bundled DDS
solver has its own permissive license (see the Redeal / DDS projects).
