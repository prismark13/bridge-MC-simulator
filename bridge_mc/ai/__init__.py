"""Optional Claude 'Explain' adapter: prompt building + streaming verdict."""
from .explain import HAVE_ANTHROPIC, build_prompt, stream_explanation

__all__ = ["HAVE_ANTHROPIC", "build_prompt", "stream_explanation"]
