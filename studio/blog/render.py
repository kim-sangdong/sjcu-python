"""블로그 렌더: content/weeks/<week>/blog/<id>.json → out/weeks/<week>/<id>/post.html, og.png, meta.json.

- post.html : 티스토리 HTML 모드에 그대로 붙여 넣는 조각(인라인 스타일). preview.html 은 사람이 브라우저로 보는 용도.
- 앱 안내 상자: 첫 소제목 앞 1회 + 본문 끝. cfg.app.link_base 가 비면 넣지 않는다.
- 관련 영상: related_video_id 의 유튜브 remote_id 를 queue.json 에서 찾아 iframe 으로 넣는다(없으면 생략).
- 표·도식이 하나도 없으면 경고(독창성 규칙: 글마다 고유 표·도식 1개 이상).
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from studio import paths
from studio.blog.html import Palette, html_token, parse, render
from studio.blog.og import render_og
from studio.cardnews.canvas import brand_of
from studio.config import Config
from studio.manifest import upsert_asset
from studio.models import BlogPost, PublishQueue, RenderedAsset, asset_kind_of, read_json, read_model, write_json
from studio.publish.utm import app_link

YOUTUBE_EMBED = "https://www.youtube.com/embed/{id}"
APP_MEDIUM = "blog"
META_DESC_MAX = 150
NO_TABLE_WARNING = "표나 도식이 하나도 없습니다 — 글마다 고유한 표(마크다운 표) 또는 도식(```flow 블록) 을 1개 이상 넣으세요(독창성 규칙)."


@dataclass
class BlogResult:
    post_id: str
    html: Path
    og: Path
    meta: Path
    warnings: list[str] = field(default_factory=list)
    info: dict = field(default_factory=dict)


def post_out_dir(week: str, post_id: str) -> Path:
    return paths.out_dir(week) / post_id


def rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(paths.ROOT.resolve()).as_posix()
    except ValueError:
        return str(p)


def _week_of_asset(asset_id: str) -> str | None:
    m = re.match(r"^(\d{4})W(\d{2})-", asset_id)
    return f"{m.group(1)}-W{m.group(2)}" if m else None


def find_youtube_id(week: str, video_asset_id: str) -> str | None:
    """queue.json 에서 이 영상의 유튜브 remote_id 를 찾는다(글 주차 → 영상 ID 의 주차 순서)."""
    weeks = [week]
    other = _week_of_asset(video_asset_id)
    if other and other not in weeks:
        weeks.append(other)
    for w in weeks:
        qp = paths.content_dir(w) / "queue.json"
        if not qp.exists():
            continue
        try:
            q = read_model(qp, PublishQueue)
        except (ValidationError, ValueError):
            continue
        t = q.find("youtube", video_asset_id)
        if t and t.remote_id and t.status not in ("failed", "skipped"):
            return t.remote_id
    return None


def _video_title(video_asset_id: str) -> str:
    w = _week_of_asset(video_asset_id)
    if not w:
        return "관련 영상"
    try:
        kind = asset_kind_of(video_asset_id)
    except KeyError:
        return "관련 영상"
    sub = {"longform": "longform", "short": "shorts"}.get(kind)
    data = read_json(paths.content_dir(w) / sub / f"{video_asset_id}.json", default={}) if sub else {}
    return str((data or {}).get("title") or "관련 영상")


def video_html(youtube_id: str, title: str, *, vertical: bool, p: Palette) -> str:
    """반응형 유튜브 임베드. 쇼츠는 세로(9:16)로 폭을 줄인다."""
    src = YOUTUBE_EMBED.format(id=_html.escape(youtube_id, quote=True))
    ratio = "177.78%" if vertical else "56.25%"
    outer = "max-width:360px;margin:28px auto;" if vertical else "margin:28px 0;"
    return (
        f'<div style="{outer}">\n'
        f'<div style="position:relative;width:100%;height:0;padding-bottom:{ratio};overflow:hidden;border-radius:12px;background:#000;">'
        f'<iframe src="{src}" title="{_html.escape(title, quote=True)}" style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;" '
        'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" '
        'referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe></div>\n'
        f'<p style="margin:8px 0 0;font-size:0.9em;color:{p.soft_text};text-align:center;">▶ {_html.escape(title)}</p>\n'
        "</div>"
    )


def _box_texts(cfg: Config) -> dict[str, str]:
    """앱 안내 문구. channel.yaml 의 platforms.tistory 에 app_box_* 키로 바꿀 수 있다."""
    t = cfg.platforms.tistory
    name = cfg.app.name.strip()
    get = lambda k, d: str(getattr(t, k, None) or (t.model_extra or {}).get(k) or d)  # noqa: E731
    return {
        "title": get("app_box_title", f"{name} 앱에서 이어서 해 보세요" if name else "앱에서 이어서 해 보세요"),
        "text": get("app_box_text", "글에서 다룬 내용을 앱에서 직접 풀어 보고 기록할 수 있어요."),
        "button": get("app_box_button", f"{name} 받기" if name else "앱 받기"),
        "note": get("app_box_note", "직접 만든 앱입니다."),
        "top": get("app_box_top", "이 글의 내용을 앱에서 바로 해 볼 수 있어요."),
    }


def app_box_html(link: str, cfg: Config, p: Palette, *, compact: bool) -> str:
    tx = {k: _html.escape(v) for k, v in _box_texts(cfg).items()}
    href = _html.escape(link, quote=True)
    if compact:
        return (
            f'<div style="margin:24px 0;padding:14px 18px;border:1px solid {p.muted};border-left:5px solid {p.primary};border-radius:10px;background:#FFFFFF;">'
            f'{tx["top"]} <a href="{href}" target="_blank" rel="noopener" style="color:{p.primary};font-weight:700;text-decoration:underline;text-underline-offset:3px;">{tx["button"]} →</a>'
            "</div>"
        )
    return (
        f'<div style="margin:40px 0 24px;padding:22px 24px;border:2px solid {p.primary};border-radius:14px;background:{p.primary_soft};">\n'
        f'<p style="margin:0 0 6px;font-size:1.1em;font-weight:800;color:{p.primary};">{tx["title"]}</p>\n'
        f'<p style="margin:0 0 16px;line-height:1.7;">{tx["text"]}</p>\n'
        f'<a href="{href}" target="_blank" rel="noopener" style="display:inline-block;padding:11px 22px;border-radius:999px;background:{p.primary};color:#FFFFFF;font-weight:700;text-decoration:none;">{tx["button"]} →</a>\n'
        f'<p style="margin:12px 0 0;font-size:0.85em;color:{p.soft_text};">{tx["note"]}</p>\n'
        "</div>"
    )


def meta_description(post: BlogPost, first_paragraph: str) -> str:
    if post.meta_description.strip():
        return post.meta_description.strip()
    text = " ".join(first_paragraph.split())
    if len(text) <= META_DESC_MAX:
        return text
    cut = text[:META_DESC_MAX]
    return (cut[: cut.rfind(" ")] if " " in cut else cut).rstrip(",.·:;") + "…"


def _tags(post: BlogPost) -> list[str]:
    out: list[str] = []
    for t in post.tags:
        t = t.strip().lstrip("#").strip()
        if t and t not in out:
            out.append(t)
    return out


def build_html(post: BlogPost, cfg: Config) -> tuple[str, dict, list[str]]:
    """(post.html 내용, 정보, 경고)."""
    p = Palette.of(brand_of(cfg))
    conv = parse(post.body_markdown, p)
    toks = conv.tokens
    warns: list[str] = []
    link = app_link(cfg, platform="tistory", medium=APP_MEDIUM, week=post.week, asset_id=post.id)

    yt_id = find_youtube_id(post.week, post.related_video_id) if post.related_video_id else None
    if post.related_video_id and not yt_id:
        warns.append(f"관련 영상 {post.related_video_id} 의 유튜브 ID 가 queue.json 에 아직 없어 임베드를 뺐습니다(영상 업로드 뒤 다시 렌더하면 들어갑니다).")

    # 뒤쪽부터 끼워 넣어야 앞쪽 위치가 바뀌지 않는다.
    inserts: list[tuple[int, str]] = []
    if yt_id:
        kind = None
        try:
            kind = asset_kind_of(post.related_video_id or "")
        except KeyError:
            pass
        vh = video_html(yt_id, _video_title(post.related_video_id or ""), vertical=kind == "short", p=p)
        pos = conv.heading_idx[1] if len(conv.heading_idx) >= 2 else len(toks)
        inserts.append((pos, vh))
    if link and conv.heading_idx:
        inserts.append((conv.heading_idx[0], app_box_html(link, cfg, p, compact=True)))
    for pos, h in sorted(inserts, key=lambda x: x[0], reverse=True):
        toks.insert(pos, html_token(h))
    if cfg.rules.disclaimer.strip():
        toks.append(html_token(f'<p style="margin:28px 0 0;font-size:0.85em;color:{p.soft_text};">{_html.escape(cfg.rules.disclaimer.strip())}</p>'))
    if link:
        toks.append(html_token(app_box_html(link, cfg, p, compact=False)))
    out = render(toks, p)

    if conv.table_count + conv.diagram_count == 0:
        warns.append(NO_TABLE_WARNING)
    bad_imgs = [s for s in conv.image_srcs if not s.startswith(("http://", "https://"))]
    if bad_imgs:
        warns.append(f"웹 주소가 아닌 이미지 {len(bad_imgs)}개({bad_imgs[0]} 등)는 티스토리에서 보이지 않습니다 — 스토리지에 올린 https 주소로 바꾸세요.")
    if not conv.heading_idx:
        warns.append("소제목(##)이 없습니다 — 검색 노출과 읽기 흐름을 위해 소제목을 2개 이상 두세요.")
    if re.search(r"<script\b", out, re.I):
        warns.append("<script> 태그가 있습니다 — 티스토리가 지우거나 글이 깨질 수 있으니 빼세요.")
    info = {
        "table_count": conv.table_count,
        "diagram_count": conv.diagram_count,
        "app_link": link,
        "video_embed": yt_id,
        "chars": len(re.sub(r"\s", "", post.body_markdown)),
        "first_paragraph": conv.first_paragraph,
    }
    return out, info, warns


PREVIEW = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;background:#F4F4F4;font-family:'Noto Sans KR','Apple SD Gothic Neo','Malgun Gothic',sans-serif;font-size:16px;">
<main style="max-width:760px;margin:0 auto;padding:32px 20px;background:#FFFFFF;">
<h1 style="font-size:1.8em;line-height:1.4;margin:0 0 24px;">{title}</h1>
{body}</main></body></html>
"""


def render_post(post: BlogPost, cfg: Config, out: Path | None = None) -> BlogResult:
    out = out or post_out_dir(post.week, post.id)
    out.mkdir(parents=True, exist_ok=True)
    html, info, warns = build_html(post, cfg)
    html_p, og_p, meta_p = out / "post.html", out / "og.png", out / "meta.json"
    html_p.write_text(html, encoding="utf-8")
    (out / "preview.html").write_text(PREVIEW.format(title=_html.escape(post.title), body=html), encoding="utf-8")
    blog = cfg.platforms.tistory.blog_name.strip()
    site = f"{blog}.tistory.com" if blog else (("@" + cfg.channel.handle.lstrip("@")) if cfg.channel.handle else "")
    warns += render_og(post.title, brand_of(cfg), og_p, key=post.id, label=post.keyword.strip()[:24], site=site)
    meta = {
        "title": post.title,
        "tags": _tags(post),
        "category": cfg.platforms.tistory.category,
        "meta_description": meta_description(post, info.pop("first_paragraph")),
    }
    write_json(meta_p, meta)
    return BlogResult(post.id, html_p, og_p, meta_p, warns, info)


def record(week: str, res: BlogResult) -> RenderedAsset:
    asset = RenderedAsset(
        asset_id=res.post_id,
        kind="blog",
        files={"html": rel(res.html), "og": rel(res.og), "meta": rel(res.meta)},
        meta={**res.info, "warnings": res.warnings},
    )
    upsert_asset(week, asset)
    return asset


def post_sources(week: str) -> list[Path]:
    return sorted((paths.content_dir(week) / "blog").glob("*.json"))


def render_week(
    week: str, cfg: Config, only: list[str] | None = None, *, log: Callable[[str], None] = print
) -> tuple[list[BlogResult], list[tuple[str, str]]]:
    """주차의 블로그 글을 모두(또는 only 만) 렌더하고 manifest 에 기록한다. (성공 목록, [(ID, 오류)])."""
    done: list[BlogResult] = []
    failed: list[tuple[str, str]] = []
    srcs = post_sources(week)
    if only:
        wanted = set(only)
        srcs = [p for p in srcs if p.stem in wanted]
        for missing in sorted(wanted - {p.stem for p in srcs}):
            failed.append((missing, f"원고가 없습니다: {rel(paths.content_dir(week) / 'blog' / (missing + '.json'))}"))
    for src in srcs:
        try:
            post = read_model(src, BlogPost)
        except ValidationError as e:
            err = e.errors()[0]
            failed.append((src.stem, f"원고 형식 오류 — {'.'.join(map(str, err['loc']))}: {err['msg']} (총 {e.error_count()}건)"))
            continue
        except ValueError as e:
            failed.append((src.stem, f"원고 JSON 을 읽지 못했습니다: {e}"))
            continue
        if post.id != src.stem or post.week != week:
            failed.append((src.stem, f"파일 안의 id/week({post.id}, {post.week})가 파일 이름·주차({src.stem}, {week})와 다릅니다."))
            continue
        try:
            res = render_post(post, cfg)
        except (OSError, ValueError) as e:
            failed.append((post.id, f"렌더 실패: {e}"))
            continue
        record(week, res)
        done.append(res)
        extra = []
        if res.info["app_link"]:
            extra.append("앱 링크")
        if res.info["video_embed"]:
            extra.append("영상")
        extra.append(f"표 {res.info['table_count']}·도식 {res.info['diagram_count']}")
        log(f"[완료] {post.id}: {rel(res.html.parent)}/ ({', '.join(extra)})")
        for w in res.warnings:
            log(f"[경고] {post.id}: {w}")
    return done, failed
