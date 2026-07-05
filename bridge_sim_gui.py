"""Launcher shim for the Bridge MC Simulator GUI.

The implementation now lives in the ``bridge_mc`` package (domain / engine /
report / ai / app). This file is kept so existing launchers and the PyInstaller
build (which target bridge_sim_gui.py) continue to work.

    GUI:      python bridge_sim_gui.py   |   python -m bridge_mc
    Headless: python -m bridge_mc.cli --help
"""
import os
import sys

# A frozen windowed app (PyInstaller --windowed) has no console, so stdout and
# stderr are None; any library that writes to them would crash at import.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from bridge_mc.app import main

if __name__ == "__main__":
    main()
