"""Bridge game/slam Monte-Carlo simulator.

Layered so the domain + engine carry no UI dependency:

    domain/   pure data + rules (no I/O, no redeal, no Qt)
    engine/   DDS solver, deal sampling, the simulation loop  (depends: domain, redeal)
    report/   HTML + text renderers                           (depends: domain)
    ai/       Claude "explain" prompt + stream                (depends: domain, anthropic)
    app/      PySide6 GUI adapter                             (depends: all of the above)
    cli.py    headless entry point                           (depends: engine, report)
"""
__version__ = "0.12.0"
