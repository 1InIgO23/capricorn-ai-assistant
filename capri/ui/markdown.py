"""A small Markdown-to-HTML converter and code highlighting for the chat."""
from __future__ import annotations

import html
import re

from ..config.theme import Theme
from .style import css_fonts

_CODE_SPAN = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC = re.compile(r"(?<![\*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\*\w])")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_BARE_URL = re.compile(r"(?<![\"'=>])(https?://[^\s<]+)")
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")


def split_segments(text: str) -> list[tuple[str, str, str]]:
    """Split a message into ``("text", content, "")`` and ``("code", content, lang)`` parts.

    An unterminated fence (while streaming) is treated as code up to the end.
    """
    segments: list[tuple[str, str, str]] = []
    buffer: list[str] = []
    in_code, lang = False, ""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                segments.append(("code", "\n".join(buffer), lang))
                buffer, in_code, lang = [], False, ""
            else:
                if any(part.strip() for part in buffer):
                    segments.append(("text", "\n".join(buffer).strip("\n"), ""))
                buffer, in_code = [], True
                lang = stripped[3:].strip().split()[0] if stripped[3:].strip() else ""
            continue
        buffer.append(line)
    if in_code:
        segments.append(("code", "\n".join(buffer), lang))
    elif any(part.strip() for part in buffer):
        segments.append(("text", "\n".join(buffer).strip("\n"), ""))
    return segments


def _inline(text: str, theme: Theme) -> str:
    """Format one line of inline Markdown as HTML."""
    spans: list[str] = []

    def stash(match: re.Match[str]) -> str:
        spans.append(match.group(1))
        return f"\x00{len(spans) - 1}\x00"

    text = _CODE_SPAN.sub(stash, text)
    text = html.escape(text, quote=False)
    text = _LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', text)

    def bare(match: re.Match[str]) -> str:
        url = match.group(1)
        trail = ""
        while url and url[-1] in ".,;:!?)":
            trail = url[-1] + trail
            url = url[:-1]
        return f'<a href="{url}">{url}</a>{trail}'

    text = _BARE_URL.sub(bare, text)
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _ITALIC.sub(r"<i>\1</i>", text)
    code_style = (f"font-family:{css_fonts(theme, 'mono')}; color:{theme.color('secondary')}; "
                  f"background-color:{theme.color('background')};")

    def unstash(match: re.Match[str]) -> str:
        return f'<span style="{code_style}">{html.escape(spans[int(match.group(1))])}</span>'

    return re.sub(r"\x00(\d+)\x00", unstash, text)


def md_to_html(text: str, theme: Theme) -> str:
    """Convert light Markdown (no code fences) to Qt rich text."""
    out: list[str] = []
    paragraph: list[str] = []
    list_kind = ""

    def flush_paragraph() -> None:
        if paragraph:
            out.append('<p style="margin:0 0 6px 0;">' + "<br>".join(paragraph) + "</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            out.append(f"</{list_kind}>")
            list_kind = ""

    def open_list(kind: str) -> None:
        nonlocal list_kind
        if list_kind != kind:
            close_list()
            out.append(f'<{kind} style="margin:0 0 6px 0; -qt-list-indent:1;">')
            list_kind = kind

    for line in text.split("\n"):
        if not line.strip():
            flush_paragraph()
            close_list()
            continue
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        numbered = _NUMBERED.match(line)
        if heading:
            flush_paragraph()
            close_list()
            out.append(f'<p style="margin:4px 0 4px 0; color:{theme.color("accent_soft")};">'
                       f"<b>{_inline(heading.group(2), theme)}</b></p>")
        elif bullet:
            flush_paragraph()
            open_list("ul")
            out.append(f"<li>{_inline(bullet.group(1), theme)}</li>")
        elif numbered:
            flush_paragraph()
            open_list("ol")
            out.append(f"<li>{_inline(numbered.group(2), theme)}</li>")
        else:
            close_list()
            paragraph.append(_inline(line, theme))
    flush_paragraph()
    close_list()
    return "".join(out)


def _pygments_style(theme: Theme):
    """Build a Pygments style class from the theme's syntax colors."""
    from pygments.style import Style
    from pygments.token import (Comment, Keyword, Name, Number, Operator, String, Text)

    class CapriStyle(Style):
        background_color = theme.color("background")
        styles = {
            Text: theme.syntax("text"),
            Comment: "italic " + theme.syntax("comment"),
            Keyword: "bold " + theme.syntax("keyword"),
            String: theme.syntax("string"),
            Number: theme.syntax("number"),
            Operator: theme.syntax("operator"),
            Name.Function: theme.syntax("function"),
            Name.Class: theme.syntax("class"),
            Name.Builtin: theme.syntax("builtin"),
            Name.Decorator: theme.syntax("function"),
        }

    return CapriStyle


_LANG_ALIASES = {"batch": "bat", "cmd": "bat", "ps1": "powershell", "py": "python",
                 "sh": "bash", "shell": "bash", "js": "javascript", "ts": "typescript"}


def highlight_code(code: str, language: str, theme: Theme) -> str:
    """Syntax-highlighted HTML (a ``<pre>`` block) for *code*."""
    font = (f"font-family:{css_fonts(theme, 'mono')}; font-size:{theme.font_size('mono_size')}pt; "
            f"color:{theme.syntax('text')}; margin:0;")
    body = html.escape(code)
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import TextLexer, get_lexer_by_name
        from pygments.util import ClassNotFound

        name = _LANG_ALIASES.get(language.lower(), language.lower())
        try:
            lexer = get_lexer_by_name(name) if name else TextLexer()
        except ClassNotFound:
            lexer = TextLexer()
        formatter = HtmlFormatter(nowrap=True, noclasses=True, style=_pygments_style(theme))
        body = highlight(code, lexer, formatter).rstrip("\n")
    except ImportError:
        pass
    return f'<pre style="{font}">{body}</pre>'
