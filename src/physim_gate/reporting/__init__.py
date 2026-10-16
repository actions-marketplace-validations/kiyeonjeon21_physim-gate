"""Renderers for comparison results: terminal, JUnit XML, Markdown and self-contained HTML."""

from physim_gate.reporting.html import render_html
from physim_gate.reporting.junit import render_junit
from physim_gate.reporting.markdown import render_markdown
from physim_gate.reporting.terminal import print_comparison

__all__ = ["print_comparison", "render_html", "render_junit", "render_markdown"]
