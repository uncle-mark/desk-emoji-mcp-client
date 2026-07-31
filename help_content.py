"""Help page content rendering for the MCP client."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


HELP_DOC_PATH = Path(__file__).parent / "docs" / "操作手册.md"


def load_help_markdown() -> str:
    try:
        return HELP_DOC_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return f"# 帮助\n\n无法读取帮助文档：{exc}"


def configure_help_tags(textbox: Any) -> None:
    _tag_config(textbox, "h1", font=("TkDefaultFont", 24, "bold"), spacing1=10, spacing3=12)
    _tag_config(textbox, "h2", font=("TkDefaultFont", 18, "bold"), spacing1=14, spacing3=8)
    _tag_config(textbox, "h3", font=("TkDefaultFont", 15, "bold"), spacing1=10, spacing3=6)
    _tag_config(textbox, "paragraph", spacing1=2, spacing3=8)
    _tag_config(textbox, "list", lmargin1=18, lmargin2=34, spacing3=4)
    _tag_config(textbox, "numbered", lmargin1=18, lmargin2=40, spacing3=4)
    _tag_config(textbox, "code", font=("TkFixedFont", 12), lmargin1=14, lmargin2=14, spacing1=4, spacing3=8)


def _tag_config(textbox: Any, tag_name: str, **kwargs: Any) -> None:
    font = kwargs.pop("font", None)
    if kwargs:
        textbox.tag_config(tag_name, **kwargs)
    if font is not None:
        textbox._textbox.tag_config(tag_name, font=font)


def render_help_markdown(textbox: Any, markdown: str) -> None:
    textbox.configure(state="normal")
    textbox.delete("1.0", "end")

    in_code = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            _insert_line(textbox, line, "code")
            continue
        if not stripped:
            textbox.insert("end", "\n")
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            _insert_line(textbox, _clean_inline_markdown(heading.group(2)), f"h{level}")
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet:
            _insert_line(textbox, f"• {_clean_inline_markdown(bullet.group(1))}", "list")
            continue

        numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if numbered:
            _insert_line(textbox, f"{numbered.group(1)}. {_clean_inline_markdown(numbered.group(2))}", "numbered")
            continue

        _insert_line(textbox, _clean_inline_markdown(stripped), "paragraph")

    textbox.configure(state="disabled")


def _insert_line(textbox: Any, text: str, tag: str) -> None:
    textbox.insert("end", text + "\n", tag)


def _clean_inline_markdown(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)
    return text
