"""Qt palette for the light/dark toggle (Fusion style)."""
from PySide6.QtGui import QColor, QPalette


def apply_palette(app, name: str):
    pal = QPalette()
    if name == "dark":
        c = QColor("#1a1a17"); t = QColor("#ecebe5"); base = QColor("#201f1c")
        pal.setColor(QPalette.Window, c); pal.setColor(QPalette.WindowText, t)
        pal.setColor(QPalette.Base, base); pal.setColor(QPalette.AlternateBase, c)
        pal.setColor(QPalette.Text, t); pal.setColor(QPalette.Button, c)
        pal.setColor(QPalette.ButtonText, t); pal.setColor(QPalette.ToolTipBase, base)
        pal.setColor(QPalette.ToolTipText, t)
        pal.setColor(QPalette.Highlight, QColor("#3d5a4b"))
        pal.setColor(QPalette.HighlightedText, t)
    app.setPalette(pal)
