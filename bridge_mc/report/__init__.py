"""Renderers that turn a :class:`SimResult` into HTML or text.

Depends only on the domain layer — no redeal, no Qt — so a report can be
produced anywhere the result object travels.
"""
from .render import render_html
from .text import render_text

__all__ = ["render_html", "render_text"]
