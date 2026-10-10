"""블로그 마크다운 → 티스토리 HTML 모드에 붙여 넣을 HTML 조각(인라인 스타일).

티스토리 스킨 CSS 를 건드릴 수 없으므로 모든 서식을 style 속성으로 넣는다.
지원: 소제목, 문단, 굵게·기울임·취소선, 링크, 인라인 코드·코드 블록, 목록, 인용, 표, 이미지, 구분선.
도식: 코드 블록 언어를 flow(또는 diagram·도식)로 쓰면 단계 흐름도를 HTML 로 그린다.

    ```flow
    제목: 신청 절차
    접수 -> 서류 심사 -> 결과 안내
    ```
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from functools import lru_cache

from markdown_it import MarkdownIt
from markdown_it.token import Token

from studio.cardnews.canvas import Brand, hex_of, mix

DIAGRAM_LANGS = {"flow", "diagram", "도식", "흐름"}


@dataclass(frozen=True)
class Palette:
    text: str
    primary: str
    accent: str
    muted: str
    bg: str
    primary_soft: str
    accent_soft: str
    soft_text: str

    @classmethod
    def of(cls, b: Brand) -> "Palette":
        return cls(
            text=hex_of(b.text),
            primary=hex_of(b.primary),
            accent=hex_of(b.accent),
            muted=hex_of(b.muted),
            bg=hex_of(b.bg),
            primary_soft=hex_of(mix(b.primary, (255, 255, 255), 0.9)),
            accent_soft=hex_of(mix(b.accent, (255, 255, 255), 0.78)),
            soft_text=hex_of(mix(b.text, (255, 255, 255), 0.4)),
        )


def _styles(p: Palette) -> dict[str, str]:
    return {
        "h2": f"margin:48px 0 18px;padding:0 0 10px;font-size:1.5em;font-weight:800;line-height:1.4;color:{p.text};border-bottom:3px solid {p.primary};",
        "h3": f"margin:36px 0 14px;padding:2px 0 2px 12px;font-size:1.25em;font-weight:700;line-height:1.45;color:{p.text};border-left:5px solid {p.accent};",
        "h4": f"margin:28px 0 10px;font-size:1.1em;font-weight:700;line-height:1.5;color:{p.primary};",
        "h5": f"margin:24px 0 8px;font-size:1em;font-weight:700;color:{p.text};",
        "h6": f"margin:20px 0 8px;font-size:0.95em;font-weight:700;color:{p.soft_text};",
        "p": "margin:0 0 18px;",
        "p_quote": "margin:0 0 8px;",
        "ul": "margin:0 0 20px;padding-left:1.4em;",
        "ol": "margin:0 0 20px;padding-left:1.6em;",
        "li": "margin:0 0 8px;",
        "blockquote": f"margin:24px 0;padding:16px 20px;border-left:5px solid {p.accent};background:{p.accent_soft};border-radius:0 10px 10px 0;color:{p.text};",
        "table_wrap": "margin:24px 0;overflow-x:auto;-webkit-overflow-scrolling:touch;",
        "table": f"width:100%;border-collapse:collapse;font-size:0.95em;line-height:1.6;border-top:2px solid {p.primary};",
        "th": f"padding:10px 12px;border:1px solid {p.muted};background:{p.primary_soft};color:{p.text};font-weight:700;text-align:left;white-space:nowrap;",
        "td": f"padding:10px 12px;border:1px solid {p.muted};vertical-align:top;",
        "tr_even": f"background:{p.bg};",
        "a": f"color:{p.primary};text-decoration:underline;text-underline-offset:3px;",
        "strong": f"font-weight:700;background:linear-gradient(transparent 62%, {p.accent_soft} 62%);",
        "em": "font-style:italic;",
        "s": f"color:{p.soft_text};",
        "code": "padding:2px 6px;border-radius:4px;background:#F3EFEA;font-family:Consolas,Menlo,monospace;font-size:0.92em;",
        "pre": "margin:0 0 20px;padding:14px 16px;border-radius:8px;background:#2B2B2B;color:#F5F5F5;overflow-x:auto;font-size:0.9em;line-height:1.6;",
        "pre_code": "font-family:Consolas,Menlo,monospace;background:none;padding:0;color:inherit;white-space:pre;",
        "hr": f"margin:36px 0;border:0;border-top:1px solid {p.muted};",
        "img": "max-width:100%;height:auto;border-radius:8px;",
        "wrap": f"line-height:1.8;word-break:keep-all;overflow-wrap:break-word;color:{p.text};",
    }


def _add_style(tok: Token, css: str) -> None:
    old = tok.attrGet("style")
    tok.attrSet("style", f"{css}{old}" if old else css)  # 마크다운 표 정렬(text-align)이 뒤에 와서 이긴다


@lru_cache(maxsize=1)
def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True, "linkify": False, "typographer": False}).enable(["table", "strikethrough"])

    def fence(self, tokens, idx, options, env):  # noqa: ANN001
        tok = tokens[idx]
        lang = (tok.info or "").strip().split(maxsplit=1)[0] if tok.info else ""
        st = env["styles"]
        if lang.lower() in DIAGRAM_LANGS:
            return flow_html(tok.content, env["palette"]) + "\n"
        return f'<pre style="{st["pre"]}"><code style="{st["pre_code"]}">{_html.escape(tok.content)}</code></pre>\n'

    def table_open(self, tokens, idx, options, env):  # noqa: ANN001
        return f'<div style="{env["styles"]["table_wrap"]}">\n' + self.renderToken(tokens, idx, options, env)

    def table_close(self, tokens, idx, options, env):  # noqa: ANN001
        return self.renderToken(tokens, idx, options, env) + "</div>\n"

    md.add_render_rule("fence", fence)
    md.add_render_rule("code_block", fence)
    md.add_render_rule("table_open", table_open)
    md.add_render_rule("table_close", table_close)
    return md


def flow_html(content: str, p: Palette) -> str:
    """단계 흐름도. 한 줄에 'A -> B -> C' 또는 한 줄에 한 단계. '제목: …' 줄은 그림 설명."""
    caption = ""
    steps: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^(?:제목|title)\s*:\s*(.+)$", line, re.I)
        if m:
            caption = m.group(1).strip()
            continue
        parts = [x.strip() for x in re.split(r"\s*(?:->|→|=>)\s*", line) if x.strip()]
        steps += [re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", x) for x in parts]
    steps = [s.replace("**", "") for s in steps if s]
    if not steps:
        return ""
    esc = _html.escape
    vertical = len(steps) > 5 or max(len(s) for s in steps) > 16
    box = f"padding:12px 16px;border:2px solid {p.primary};border-radius:12px;background:#FFFFFF;font-weight:700;line-height:1.5;text-align:center;"
    arrow = f"color:{p.accent};font-weight:800;font-size:1.3em;line-height:1;"
    out = ['<figure style="margin:28px 0;padding:20px 16px;border-radius:14px;background:' + p.primary_soft + ';">']
    if vertical:
        out.append('<div style="max-width:560px;margin:0 auto;">')
        for i, s in enumerate(steps):
            if i:
                out.append(f'<div style="{arrow}text-align:center;margin:6px 0;">↓</div>')
            badge = f'<span style="display:inline-block;min-width:1.6em;margin-right:8px;padding:0 6px;border-radius:999px;background:{p.primary};color:#FFFFFF;font-size:0.85em;">{i + 1}</span>'
            out.append(f'<div style="{box}text-align:left;">{badge}{esc(s)}</div>')
        out.append("</div>")
    else:
        out.append('<div style="display:flex;flex-wrap:wrap;align-items:center;justify-content:center;gap:8px;">')
        for i, s in enumerate(steps):
            if i:
                out.append(f'<span style="{arrow}">→</span>')
            out.append(f'<div style="{box}min-width:72px;">{esc(s)}</div>')
        out.append("</div>")
    if caption:
        out.append(f'<figcaption style="margin-top:12px;text-align:center;font-size:0.9em;color:{p.soft_text};">{esc(caption)}</figcaption>')
    out.append("</figure>")
    return "\n".join(out)


@dataclass
class Converted:
    tokens: list[Token]
    table_count: int = 0
    diagram_count: int = 0
    heading_idx: list[int] = field(default_factory=list)   # 최상위 소제목 시작 토큰 위치
    image_srcs: list[str] = field(default_factory=list)
    first_paragraph: str = ""


def parse(markdown: str, p: Palette) -> Converted:
    """마크다운을 토큰으로 바꾸고 인라인 스타일을 입힌다."""
    st = _styles(p)
    tokens = _parser().parse(markdown or "")
    shift = 1 if any(t.type == "heading_open" and t.tag == "h1" for t in tokens) else 0  # 글 제목이 h1 이므로 본문 h1 은 한 단계 내린다
    conv = Converted(tokens)
    parents: list[str] = []
    in_tbody, row = False, 0
    for i, t in enumerate(tokens):
        if t.type in ("heading_open", "heading_close") and shift:
            t.tag = f"h{min(6, int(t.tag[1]) + shift)}"
        if t.type == "heading_open" and t.level == 0:
            conv.heading_idx.append(i)
        if t.type == "table_open":
            conv.table_count += 1
        if t.type in ("fence", "code_block"):
            lang = (t.info or "").split()
            if lang and lang[0].lower() in DIAGRAM_LANGS:
                conv.diagram_count += 1
        if t.type in ("html_block",):
            low = t.content.lower()
            conv.table_count += low.count("<table")
            conv.diagram_count += low.count("<svg") + low.count("<figure")
        if t.type == "tbody_open":
            in_tbody, row = True, 0
        elif t.type == "tbody_close":
            in_tbody = False
        if t.nesting == 1 or t.type == "hr":
            tag = t.tag
            if tag == "p" and "blockquote" in parents:
                _add_style(t, st["p_quote"])
            elif tag in st:
                _add_style(t, st[tag])
            if t.type == "tr_open" and in_tbody:
                row += 1
                if row % 2 == 0:
                    _add_style(t, st["tr_even"])
        if t.type == "inline" and t.children:
            if not conv.first_paragraph and i > 0 and tokens[i - 1].type == "paragraph_open" and t.level == 1:
                conv.first_paragraph = _plain(t)
            for c in t.children:
                if c.type in ("strong_open", "em_open", "s_open"):
                    _add_style(c, st[c.tag])
                elif c.type == "link_open":
                    _add_style(c, st["a"])
                    href = c.attrGet("href") or ""
                    if href.startswith(("http://", "https://")):
                        c.attrSet("target", "_blank")
                        c.attrSet("rel", "noopener")
                elif c.type == "code_inline":
                    _add_style(c, st["code"])
                elif c.type == "image":
                    _add_style(c, st["img"])
                    conv.image_srcs.append(str(c.attrGet("src") or ""))
                elif c.type == "html_inline":
                    low = c.content.lower()
                    conv.diagram_count += low.count("<svg")
        if t.nesting == 1:
            parents.append(t.tag)
        elif t.nesting == -1 and parents:
            parents.pop()
    return conv


def _plain(inline: Token) -> str:
    """인라인 토큰의 글자만(링크 글자 포함, 주소·서식 제외)."""
    parts = []
    for c in inline.children or []:
        if c.type in ("text", "code_inline"):
            parts.append(c.content)
        elif c.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return " ".join("".join(parts).split())


def html_token(content: str) -> Token:
    tok = Token("html_block", "", 0)
    tok.content = content.rstrip("\n") + "\n"
    return tok


def render(tokens: list[Token], p: Palette) -> str:
    st = _styles(p)
    body = _parser().renderer.render(tokens, _parser().options, {"styles": st, "palette": p})
    return f'<div style="{st["wrap"]}">\n{body.rstrip()}\n</div>\n'
