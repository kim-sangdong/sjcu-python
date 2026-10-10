"""티스토리 발행기(블로그 글) — Playwright 로 본인 계정 로그인 세션을 써서 글쓰기 화면을 조작한다.

왜 브라우저 자동화인가: 글쓰기를 포함한 티스토리 Open API 는 2024년 2월 종료됐다 [기억·미확인]. 공식 대체 API 가 없다.

⚠ 약관 위험 — 운영 전에 반드시 읽을 것 (docs/research/facts-meta-tiktok-tistory.md §4.2·§4.3)
- 카카오 통합서비스약관·티스토리 운영정책은 자동화 수단을 쓴 비정상 이용과 스팸·대량 자동 게시를 제한한다 [기억·미확인].
  제재가 같은 카카오계정의 다른 서비스로 번질 수 있다 [추측]. 본인 블로그에 본인 글만, 적은 양(실행당 기본 2편)으로 올린다.
- 더 보수적으로 쓰려면 channel.yaml `platforms.tistory.mode: draft` — 임시저장까지만 자동화하고 발행 버튼은 사람이 누른다
  (조사 보고서의 권고안). 발행 뒤 `python -m studio publish mark --platform tistory --remote-id <글 주소>` 로 연결한다.
- 위험을 받아들일 수 없으면 블로그를 Blogger API(공식 글쓰기 API)로 옮긴다(§4.3).

세션: TISTORY_STORAGE_STATE_B64(Playwright storageState JSON 을 base64, CI 용) 또는 .secrets/tistory_state.json.
      `python -m studio auth tistory` 가 화면 있는 컴퓨터에서 브라우저를 띄워 카카오 로그인 후 두 가지를 모두 만든다.
      로그인 페이지로 튕기면 세션 만료 → PublishError(retryable=False).

흐름: https://<blog_name>.tistory.com/manage/newpost/ → (임시저장 이어쓰기 확인창은 거절) → HTML 모드 전환(확인창 수락)
      → 제목 → 본문(out/weeks/<W>/<ID>/post.html) → 태그(meta.json tags) → 카테고리(platforms.tistory.category)
      → 완료(발행 레이어) → 공개 설정(visibility) → 발행 → 글 주소 확인.
- 셀렉터는 모두 [추측]이다(커뮤니티 코드 기억). 아래 SEL_* 상수에 여러 후보와 텍스트 기반 대체를 두었고, 실제 DOM 이 바뀌면
  `python -m playwright codegen https://<blog>.tistory.com/manage/newpost/` 로 확인해 상수만 고친다.
- 실패하면 out/logs/tistory-<ID>-<시각>.png(스크린샷)와 .html(DOM)을 남긴다.
- 입력 사이 대기는 무작위가 아닌 결정적 짧은 값(step_delay_ms·type_delay_ms)이다.
- 발행 버튼을 누른 뒤의 오류는 재시도하지 않는다(글이 두 번 올라갈 수 있음) — 블로그에서 확인 후 mark/retry.

설정(channel.yaml platforms.tistory, blog_name 외에는 선택):
  blog_name: myblog          # https://myblog.tistory.com
  category: "공부법"          # 블로그 카테고리 이름(정확히 같은 글자). 비우면 카테고리 없음
  visibility: public         # public | protected | private
  mode: publish              # publish | draft
  headless: true
  timeout_seconds: 30        # 화면 이동·발행 확인 대기
  step_timeout_seconds: 10   # 요소 하나를 찾는 대기
  step_delay_ms: 400
  type_delay_ms: 25
  executable_path: null      # 크로미움 경로(기본 설치본이 없을 때)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

from studio import paths
from studio.config import Config, env, load_config
from studio.models import BlogPost
from studio.publish.base import PublishContext, PublishError, PublishResult

# ---------------------------------------------------------------- 상수 (화면이 바뀌면 여기만 고친다)

NEWPOST_URL = "https://{blog}.tistory.com/manage/newpost/"
LOGIN_URL = "https://www.tistory.com/auth/login"
POST_URL_RE = r"^https?://{blog}\.tistory\.com/(?:entry/[^/?#]+|\d+)/?(?:[?#].*)?$"
LOGIN_URL_MARKERS = ("/auth/login", "accounts.kakao.com", "kauth.kakao.com", "logins.daum.net")
SESSION_COOKIES = ("TSSESSION",)                      # [추측] 티스토리 로그인 쿠키 이름
ENV_STATE_B64 = "TISTORY_STORAGE_STATE_B64"
STATE_FILE = "tistory_state.json"
STATE_B64_FILE = "tistory_state.b64"
CHROMIUM_FALLBACKS = ("/opt/pw-browsers/chromium",)  # 설치된 playwright 와 브라우저 버전이 다를 때
VIEWPORT = {"width": 1280, "height": 900}
TAG_MAX = 10                                          # [기억·미확인] 티스토리 태그 개수 상한
POLL_MS = 200
DRYRUN_PREFIX = "dryrun-"
VISIBILITIES = ("public", "protected", "private")

# 셀렉터 후보 — 앞에서부터 '보이는' 첫 요소를 쓴다. 모두 [추측].
SEL_TITLE = ("textarea#post-title-inp", "#post-title-inp", "textarea[placeholder*='제목']", "input[placeholder*='제목']")
SEL_MODE_MENU = ("#editor-mode-layer-btn-open", "button:has-text('기본모드')", "button:has-text('모드')")
SEL_MODE_HTML = ("#editor-mode-html", "[data-mode='html']", "#editor-mode-layer-btn :text-is('HTML')", "text=HTML")
SEL_CODEMIRROR = ".CodeMirror"                         # HTML 모드 편집기(CodeMirror 인스턴스를 JS 로 setValue)
SEL_HTML_TEXTAREA = ("#html-editor textarea", ".html-editor textarea", "textarea.html-editor", "#html-editor-textarea")
SEL_TAG_INPUT = ("input#tagText", "#tagText", "input[placeholder*='태그']")
SEL_CATEGORY_BUTTON = ("#category-btn", "button:has-text('카테고리')")
SEL_CATEGORY_ITEMS = ("#category-list .mce-menu-item", "#category-list [role='option']", "#category-list li", "[class*='category'] [role='option']")
SEL_COMPLETE = ("#publish-layer-btn", "button:has-text('완료')")
SEL_VISIBILITY = {
    "public": ("#open20", "label[for='open20']", "label:text-is('공개')", "text=\"공개\""),
    "protected": ("#open15", "label[for='open15']", "label:text-is('보호')", "text=\"보호\""),
    "private": ("#open0", "label[for='open0']", "label:text-is('비공개')", "text=\"비공개\""),
}
SEL_VISIBILITY_RADIO = {"public": "#open20", "protected": "#open15", "private": "#open0"}
SEL_PUBLISH = ("#publish-btn", "button:has-text('공개 발행')", "button:has-text('발행')")
SEL_DRAFT = ("button:has-text('임시저장')", "a:has-text('임시저장')", ".btn-draft")
SEL_SUCCESS = ("text=발행되었습니다", "text=저장되었습니다", "text=임시저장 되었습니다")
SEL_ERROR = (".layer_error", ".alert-error", "text=실패했습니다")
SEL_POST_LINKS = ("a[href*='.tistory.com/']", "a[href*='/entry/']", "a")
DIALOG_DISMISS_HINTS = ("이어서", "저장된 글", "작성 중인 글")   # 임시저장 이어쓰기 확인창은 거절, 나머지(모드 전환 등)는 수락


@dataclass(frozen=True)
class Selectors:
    title: tuple[str, ...] = SEL_TITLE
    mode_menu: tuple[str, ...] = SEL_MODE_MENU
    mode_html: tuple[str, ...] = SEL_MODE_HTML
    codemirror: str = SEL_CODEMIRROR
    html_textarea: tuple[str, ...] = SEL_HTML_TEXTAREA
    tag_input: tuple[str, ...] = SEL_TAG_INPUT
    category_button: tuple[str, ...] = SEL_CATEGORY_BUTTON
    category_items: tuple[str, ...] = SEL_CATEGORY_ITEMS
    complete: tuple[str, ...] = SEL_COMPLETE
    visibility: dict[str, tuple[str, ...]] = field(default_factory=lambda: dict(SEL_VISIBILITY))
    visibility_radio: dict[str, str] = field(default_factory=lambda: dict(SEL_VISIBILITY_RADIO))
    publish: tuple[str, ...] = SEL_PUBLISH
    draft: tuple[str, ...] = SEL_DRAFT
    success: tuple[str, ...] = SEL_SUCCESS
    error: tuple[str, ...] = SEL_ERROR
    post_links: tuple[str, ...] = SEL_POST_LINKS


@dataclass
class FlowOptions:
    blog_name: str
    newpost_url: str
    selectors: Selectors = field(default_factory=Selectors)
    mode: str = "publish"                 # publish | draft
    visibility: str = "public"
    category: str = ""
    headless: bool = True
    timeout_ms: int = 30_000
    step_timeout_ms: int = 10_000
    step_delay_ms: int = 400
    type_delay_ms: int = 25
    executable_path: str | None = None
    log_dir: Path | None = None           # None → out/logs
    post_url_re: str | None = None        # None → POST_URL_RE(blog_name)
    login_markers: tuple[str, ...] = LOGIN_URL_MARKERS


@dataclass
class PostInput:
    asset_id: str
    title: str
    html: str
    tags: list[str]
    source: str = "post.html"


@dataclass
class FlowResult:
    mode: str
    url: str | None = None
    remote_id: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class Progress:
    step: str = "시작"
    submitted: bool = False               # 발행(또는 임시저장) 버튼을 눌렀는가 — 이후 오류는 재시도 금지


class SessionExpired(PublishError):
    def __init__(self, url: str = ""):
        where = f"({url.split('?')[0]})" if url else ""
        super().__init__(
            f"티스토리 로그인 세션이 만료됐습니다{where}. 화면 있는 컴퓨터에서 `python -m studio auth tistory` 로 다시 로그인하고, "
            f"CI 는 새로 만든 base64 를 {ENV_STATE_B64} 시크릿에 넣은 뒤 `publish retry` 하세요.",
            retryable=False,
        )


class FlowError(PublishError):
    pass


def _is_dry(ctx: PublishContext) -> bool:
    return bool(ctx.dry_run) or os.environ.get("STUDIO_DRY_RUN", "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------- 설정·입력

def normalize_blog_name(value: str) -> str:
    """'https://myblog.tistory.com/' 처럼 넣어도 'myblog' 만 남긴다."""
    v = value.strip().lower()
    v = re.sub(r"^https?://", "", v).split("/")[0]
    return v.removesuffix(".tistory.com")


def flow_options(cfg: Config, **overrides: Any) -> FlowOptions:
    t = cfg.platforms.tistory
    extra = t.model_extra or {}
    blog = normalize_blog_name(t.blog_name or "")
    if not blog or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", blog):
        raise PublishError(
            "channel.yaml platforms.tistory.blog_name 이 비었거나 형식이 틀렸습니다 — https://<이름>.tistory.com 의 <이름> 을 넣으세요.",
            retryable=False,
        )
    mode = str(extra.get("mode", "publish")).strip().lower()
    if mode not in ("publish", "draft"):
        raise PublishError(f"platforms.tistory.mode 값 {mode!r} 을 알 수 없습니다(publish | draft).", retryable=False)
    opts = FlowOptions(
        blog_name=blog,
        newpost_url=NEWPOST_URL.format(blog=blog),
        mode=mode,
        visibility=t.visibility,
        category=(t.category or "").strip(),
        headless=bool(extra.get("headless", True)),
        timeout_ms=int(float(extra.get("timeout_seconds", 30)) * 1000),
        step_timeout_ms=int(float(extra.get("step_timeout_seconds", 10)) * 1000),
        step_delay_ms=int(extra.get("step_delay_ms", 400)),
        type_delay_ms=int(extra.get("type_delay_ms", 25)),
        executable_path=extra.get("executable_path") or None,
    )
    return replace(opts, **overrides) if overrides else opts


def clean_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        t = re.sub(r"[,#]", " ", str(t)).strip()
        t = re.sub(r"\s+", " ", t)
        if t and t not in out:
            out.append(t)
    return out[:TAG_MAX]


def _body_html(ctx: PublishContext, post: BlogPost) -> tuple[str, str]:
    """(본문 HTML, 출처). post.html 을 쓰되, 없거나 관련 영상 임베드가 빠졌으면 원고에서 다시 만든다."""
    rel = ctx.asset.files.get("html")
    path = paths.ROOT / rel if rel else None
    has_file = bool(path and path.exists())
    stale_embed = bool(post.related_video_id) and not ctx.asset.meta.get("video_embed")
    if has_file and not stale_embed:
        return path.read_text(encoding="utf-8"), "post.html"  # type: ignore[union-attr]
    try:
        from studio.blog.render import build_html

        html, info, _ = build_html(post, ctx.cfg)
    except Exception as e:  # noqa: BLE001 — 렌더 모듈 오류여도 렌더 결과 파일이 있으면 그것을 쓴다
        if has_file:
            return path.read_text(encoding="utf-8"), "post.html"  # type: ignore[union-attr]
        raise PublishError(
            f"{post.id}: post.html 이 없고 원고에서 다시 만들지도 못했습니다({type(e).__name__}: {str(e)[:120]}). "
            f"`python -m studio render blog --week {ctx.week} --only {post.id}` 를 실행하세요.",
            retryable=False,
        ) from None
    if has_file and not info.get("video_embed"):
        return path.read_text(encoding="utf-8"), "post.html"  # type: ignore[union-attr]
    return html, "원고에서 다시 렌더" + (" (관련 영상 임베드 추가)" if info.get("video_embed") else "")


def build_post_input(ctx: PublishContext) -> PostInput:
    post = ctx.content
    if not isinstance(post, BlogPost):
        raise PublishError(f"{ctx.asset.asset_id}: 티스토리는 블로그 원고(BlogPost)만 올립니다.", retryable=False)
    meta: dict[str, Any] = {}
    mrel = ctx.asset.files.get("meta")
    if mrel and (paths.ROOT / mrel).exists():
        try:
            meta = json.loads((paths.ROOT / mrel).read_text(encoding="utf-8")) or {}
        except ValueError:
            ctx.log.append("meta.json 을 읽지 못해 원고의 제목·태그를 씁니다.")
    title = str(meta.get("title") or post.title).strip()
    if not title:
        raise PublishError(f"{post.id}: 제목이 비어 있습니다.", retryable=False)
    html, source = _body_html(ctx, post)
    if not html.strip():
        raise PublishError(f"{post.id}: 본문 HTML 이 비어 있습니다. `render blog` 를 다시 하세요.", retryable=False)
    return PostInput(asset_id=post.id, title=title, html=html, tags=clean_tags(list(meta.get("tags") or post.tags)), source=source)


def state_path() -> Path:
    return paths.SECRETS / STATE_FILE


def load_storage_state() -> dict[str, Any]:
    """TISTORY_STORAGE_STATE_B64 → .secrets/tistory_state.json 순서로 Playwright storageState 를 읽는다."""
    raw = (env(ENV_STATE_B64, required=False) or "").strip()
    if raw:
        try:
            data = json.loads(base64.b64decode(raw).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise PublishError(
                f"{ENV_STATE_B64} 값을 해석하지 못했습니다(storageState JSON 을 base64 로 인코딩한 값이어야 합니다). "
                "`python -m studio auth tistory` 가 만든 파일 내용을 그대로 넣으세요.",
                retryable=False,
            ) from None
        where = ENV_STATE_B64
    elif state_path().exists():
        try:
            data = json.loads(state_path().read_text(encoding="utf-8"))
        except ValueError:
            raise PublishError(f"{state_path()} 가 손상됐습니다. `python -m studio auth tistory` 로 다시 만드세요.", retryable=False) from None
        where = str(state_path())
    else:
        raise PublishError(
            f"티스토리 로그인 세션이 없습니다. 화면 있는 컴퓨터에서 `python -m studio auth tistory` 를 실행하고, "
            f"CI 에는 만들어진 base64 를 {ENV_STATE_B64} 시크릿으로 넣으세요.",
            retryable=False,
        )
    if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
        raise PublishError(f"{where}: Playwright storageState 형식이 아닙니다(cookies 목록 없음). `auth tistory` 로 다시 만드세요.", retryable=False)
    return {"cookies": data["cookies"], "origins": data.get("origins") or []}


# ---------------------------------------------------------------- 브라우저 조작 (page 는 Playwright sync Page)

def is_login_url(url: str, markers: tuple[str, ...] = LOGIN_URL_MARKERS) -> bool:
    return any(m in url for m in markers)


def _pause(page, ms: int) -> None:
    if ms > 0:
        page.wait_for_timeout(ms)


def _visible_first(page, candidates: tuple[str, ...]):
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                return loc
        except Exception:  # noqa: BLE001 — 잘못된 후보 셀렉터·분리된 요소는 다음 후보로
            continue
    return None


def _find(page, candidates: tuple[str, ...], timeout_ms: int, what: str):
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        loc = _visible_first(page, candidates)
        if loc is not None:
            return loc
        if time.monotonic() >= deadline:
            raise FlowError(
                f"티스토리 글쓰기 화면에서 '{what}' 요소를 찾지 못했습니다(후보: {', '.join(candidates)}). "
                "화면 구조가 바뀌었을 수 있습니다 — 스크린샷을 보고 studio/publish/tistory.py 의 SEL_* 상수를 고치세요."
            )
        page.wait_for_timeout(POLL_MS)


def _wait_editor(page, opts: FlowOptions):
    """제목 칸이 보일 때까지 기다린다. 도중에 로그인 페이지로 가면 세션 만료."""
    deadline = time.monotonic() + opts.timeout_ms / 1000
    while True:
        if is_login_url(page.url, opts.login_markers):
            raise SessionExpired(page.url)
        loc = _visible_first(page, opts.selectors.title)
        if loc is not None:
            return loc
        if time.monotonic() >= deadline:
            raise FlowError(
                f"글쓰기 화면이 열리지 않았습니다(현재 주소 {page.url.split('?')[0]}). blog_name 이 본인 블로그인지, "
                "티스토리 화면이 바뀌지 않았는지 스크린샷으로 확인하세요."
            )
        page.wait_for_timeout(POLL_MS)


_JS_CM_SET = """([sel, html]) => {
  const el = document.querySelector(sel);
  if (!el || !el.CodeMirror) return false;
  el.CodeMirror.setValue(html);
  if (el.CodeMirror.save) el.CodeMirror.save();
  return true;
}"""
_JS_CM_GET = """(sel) => { const el = document.querySelector(sel); return el && el.CodeMirror ? el.CodeMirror.getValue() : null; }"""


def _html_editor_visible(page, s: Selectors) -> bool:
    return _visible_first(page, (s.codemirror, *s.html_textarea)) is not None


def _switch_html(page, opts: FlowOptions, log: list[str]) -> None:
    s = opts.selectors
    if _html_editor_visible(page, s):
        log.append("tistory: 이미 HTML 모드")
        return
    _find(page, s.mode_menu, opts.step_timeout_ms, "편집 모드 메뉴").click()
    _pause(page, opts.step_delay_ms)
    _find(page, s.mode_html, opts.step_timeout_ms, "HTML 모드").click()   # 전환 확인창은 _on_dialog 가 수락
    _find(page, (s.codemirror, *s.html_textarea), opts.step_timeout_ms, "HTML 편집기")
    log.append("tistory: HTML 모드 전환")


def _norm_html(s: str) -> str:
    return s.replace("\r\n", "\n").strip()


def _set_body(page, html: str, opts: FlowOptions, log: list[str]) -> None:
    s = opts.selectors
    how = "CodeMirror"
    if not page.evaluate(_JS_CM_SET, [s.codemirror, html]):
        how = "textarea"
        _find(page, s.html_textarea, opts.step_timeout_ms, "HTML 본문 입력칸").fill(html)
    _pause(page, opts.step_delay_ms)
    got = page.evaluate(_JS_CM_GET, s.codemirror) if how == "CodeMirror" else _visible_first(page, s.html_textarea).input_value()
    if _norm_html(got or "") != _norm_html(html):
        raise FlowError(f"본문 입력을 확인하지 못했습니다({how}: {len(got or '')}자 / 기대 {len(html)}자). 편집기 구조가 바뀌었는지 확인하세요.")
    log.append(f"tistory: 본문 {len(html)}자 입력({how})")


def _type_title(page, title: str, opts: FlowOptions, el, log: list[str]) -> None:
    el.click()
    el.fill("")
    el.press_sequentially(title, delay=opts.type_delay_ms)
    _pause(page, opts.step_delay_ms)
    got = el.input_value()
    if got.strip() != title.strip():
        el.fill(title)   # 자동완성·IME 로 어긋나면 한 번에 채운다
        if el.input_value().strip() != title.strip():
            raise FlowError(f"제목 입력을 확인하지 못했습니다(입력값 {got[:30]!r}).")
    log.append(f"tistory: 제목 입력 {title[:40]!r}")


def _add_tags(page, tags: list[str], opts: FlowOptions, log: list[str]) -> list[str]:
    if not tags:
        return []
    el = _visible_first(page, opts.selectors.tag_input)
    if el is None:
        return [f"태그 입력칸을 찾지 못해 태그 {len(tags)}개를 넣지 못했습니다(SEL_TAG_INPUT 확인)."]
    for t in tags:
        el.fill(t)
        el.press("Enter")
        _pause(page, max(opts.step_delay_ms // 4, 0))
    log.append(f"tistory: 태그 {len(tags)}개")
    return []


def _norm_label(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lstrip("-·").strip())


def _pick_category(page, name: str, opts: FlowOptions, log: list[str]) -> list[str]:
    s = opts.selectors
    warn = [f"카테고리 '{name}' 를 찾지 못해 '카테고리 없음'으로 올렸습니다 — channel.yaml platforms.tistory.category 를 "
            "블로그 카테고리 이름과 똑같이 고치세요."]
    btn = _visible_first(page, s.category_button)
    if btn is None:
        return warn
    btn.click()
    _pause(page, opts.step_delay_ms)
    want = _norm_label(name)
    deadline = time.monotonic() + opts.step_timeout_ms / 1000
    while time.monotonic() < deadline:
        for sel in s.category_items:
            items = page.locator(sel)
            for i in range(min(items.count(), 200)):
                it = items.nth(i)
                try:
                    if _norm_label(it.inner_text()) == want and it.is_visible():
                        it.click()
                        log.append(f"tistory: 카테고리 {name!r}")
                        return []
                except Exception:  # noqa: BLE001
                    continue
        page.wait_for_timeout(POLL_MS)
    page.keyboard.press("Escape")
    return warn


def _set_visibility(page, opts: FlowOptions, log: list[str]) -> None:
    s = opts.selectors
    cands = s.visibility.get(opts.visibility)
    if not cands:
        raise FlowError(f"공개 설정 {opts.visibility!r} 을 알 수 없습니다({' | '.join(VISIBILITIES)}).", retryable=False)
    el = _find(page, cands, opts.step_timeout_ms, f"공개 설정({opts.visibility})")
    kind = el.evaluate("e => (e.tagName + ':' + (e.type || '')).toLowerCase()")
    if kind == "input:radio":
        el.check()
    else:
        el.click()
    _pause(page, opts.step_delay_ms)
    radio = s.visibility_radio.get(opts.visibility)
    if radio and page.locator(radio).count():
        if not page.locator(radio).first.is_checked():
            raise FlowError(f"공개 설정을 {opts.visibility} 로 바꾸지 못했습니다 — 잘못된 공개 범위로 올라가지 않게 멈췄습니다.")
    else:
        log.append("tistory: 공개 설정 라디오를 찾지 못해 선택 결과는 확인하지 않았습니다.")
    log.append(f"tistory: 공개 설정 {opts.visibility}")


def _wait_done(page, editor_url: str, opts: FlowOptions) -> None:
    """발행 뒤 화면 이동(또는 성공 표시)을 기다린다. 오류 표시가 보이면 그 내용으로 실패."""
    s = opts.selectors
    deadline = time.monotonic() + opts.timeout_ms / 1000
    while True:
        if page.url != editor_url:
            return
        if _visible_first(page, s.success) is not None:
            return
        err = _visible_first(page, s.error)
        if err is not None:
            raise FlowError(f"티스토리가 발행 오류를 표시했습니다: {err.inner_text()[:200]!r}", retryable=False)
        if time.monotonic() >= deadline:
            raise FlowError("발행 버튼을 눌렀지만 결과를 확인하지 못했습니다.", retryable=False)
        page.wait_for_timeout(POLL_MS)


def post_id_of(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"/(\d+)/?(?:[?#]|$)", url)
    return m.group(1) if m else url


def find_post_url(page, title: str, opts: FlowOptions) -> str | None:
    """발행 뒤 화면에서 글 주소를 찾는다: 지금 주소가 글이면 그것, 아니면(글 관리 목록 등) 제목이 같은 링크."""
    pat = re.compile(opts.post_url_re or POST_URL_RE.format(blog=re.escape(opts.blog_name)))
    probe = title.strip()[:20]
    deadline = time.monotonic() + opts.step_timeout_ms / 1000
    while True:
        if pat.match(page.url):
            return page.url
        for sel in opts.selectors.post_links:
            try:
                links = page.locator(sel).filter(has_text=probe)
                for i in range(min(links.count(), 30)):
                    href = links.nth(i).get_attribute("href")
                    if href:
                        full = urljoin(page.url, href)
                        if pat.match(full):
                            return full
            except Exception:  # noqa: BLE001 — 화면 이동 중에는 요소 조회가 실패할 수 있다
                continue
        if time.monotonic() >= deadline:
            return None
        page.wait_for_timeout(POLL_MS)


def run_flow(page, post: PostInput, opts: FlowOptions, log: list[str], progress: Progress | None = None) -> FlowResult:
    """열린 page 에서 글쓰기 → 발행(또는 임시저장)까지. 테스트는 file:// 가짜 페이지와 셀렉터 주입으로 돈다."""
    progress = progress or Progress()
    s = opts.selectors
    warnings: list[str] = []

    progress.step = "글쓰기 화면 열기"
    page.goto(opts.newpost_url, wait_until="domcontentloaded")
    title_el = _wait_editor(page, opts)
    editor_url = page.url
    log.append(f"tistory: 글쓰기 화면 {editor_url.split('?')[0]}")
    _pause(page, opts.step_delay_ms)

    progress.step = "HTML 모드 전환"
    _switch_html(page, opts, log)
    _pause(page, opts.step_delay_ms)

    progress.step = "제목 입력"
    title_el = _visible_first(page, s.title) or title_el
    _type_title(page, post.title, opts, title_el, log)

    progress.step = "본문 입력"
    _set_body(page, post.html, opts, log)

    progress.step = "태그 입력"
    warnings += _add_tags(page, post.tags, opts, log)

    if opts.category:
        progress.step = "카테고리 선택"
        warnings += _pick_category(page, opts.category, opts, log)
    _pause(page, opts.step_delay_ms)

    if opts.mode == "draft":
        progress.step = "임시저장"
        btn = _find(page, s.draft, opts.step_timeout_ms, "임시저장 버튼")
        progress.submitted = True
        btn.click()
        deadline = time.monotonic() + opts.step_timeout_ms / 1000
        while _visible_first(page, s.success) is None and time.monotonic() < deadline:
            page.wait_for_timeout(POLL_MS)
        if _visible_first(page, s.success) is None:
            warnings.append("임시저장 완료 표시를 보지 못했습니다 — 티스토리 글 관리 > 임시저장 목록에서 확인하세요.")
        log.append("tistory: 임시저장")
        return FlowResult(mode="draft", warnings=warnings)

    progress.step = "발행 레이어 열기"
    _find(page, s.complete, opts.step_timeout_ms, "완료(발행 레이어)").click()
    _pause(page, opts.step_delay_ms)

    progress.step = "공개 설정"
    _set_visibility(page, opts, log)

    progress.step = "발행"
    btn = _find(page, s.publish, opts.step_timeout_ms, "발행 버튼")
    progress.submitted = True
    btn.click()
    _wait_done(page, editor_url, opts)
    log.append("tistory: 발행 버튼 누름 → 화면 이동 확인")

    progress.step = "글 주소 확인"
    url = find_post_url(page, post.title, opts)
    if not url:
        warnings.append("발행은 됐지만 글 주소를 찾지 못했습니다 — 블로그에서 확인하고 `publish mark --platform tistory --remote-id <글 주소>` 로 연결하세요.")
    else:
        log.append(f"tistory: 글 주소 {url}")
    return FlowResult(mode="publish", url=url, remote_id=post_id_of(url), warnings=warnings)


def capture(page, asset_id: str, log_dir: Path | None = None) -> list[Path]:
    """실패 화면 스크린샷과 DOM(HTML)을 out/logs 에 남긴다."""
    d = log_dir or (paths.OUT / "logs")
    d.mkdir(parents=True, exist_ok=True)
    stem = d / f"tistory-{asset_id}-{datetime.now(paths.KST):%Y%m%d-%H%M%S}"
    saved: list[Path] = []
    try:
        page.screenshot(path=str(stem.with_suffix(".png")), full_page=True)
        saved.append(stem.with_suffix(".png"))
    except Exception:  # noqa: BLE001
        pass
    try:
        stem.with_suffix(".html").write_text(page.content(), encoding="utf-8")
        saved.append(stem.with_suffix(".html"))
    except Exception:  # noqa: BLE001
        pass
    return saved


def _on_dialog(dialog, log: list[str]) -> None:
    try:
        msg = (dialog.message or "").strip()
        if dialog.type != "beforeunload" and any(h in msg for h in DIALOG_DISMISS_HINTS):
            log.append(f"tistory: 확인창 거절 {msg[:40]!r}")
            dialog.dismiss()
        else:
            log.append(f"tistory: 확인창 수락 {msg[:40]!r}")
            dialog.accept()
    except Exception:  # noqa: BLE001 — 이미 닫힌 확인창
        pass


def launch_browser(pw, *, headless: bool = True, executable_path: str | None = None):
    """크로미움을 띄운다. 설치된 playwright 의 브라우저가 없으면 CHROMIUM_FALLBACKS 를 쓴다."""
    from playwright.sync_api import Error as PlaywrightError

    if executable_path:
        return pw.chromium.launch(headless=headless, executable_path=executable_path)
    try:
        return pw.chromium.launch(headless=headless)
    except PlaywrightError as e:
        if "Executable doesn't exist" not in str(e):
            raise
        for cand in CHROMIUM_FALLBACKS:
            if Path(cand).exists():
                return pw.chromium.launch(headless=headless, executable_path=cand)
        raise PublishError(
            "크로미움이 설치돼 있지 않습니다. `python -m playwright install chromium` 을 실행하거나 "
            "channel.yaml platforms.tistory.executable_path 에 경로를 넣으세요.",
            retryable=False,
        ) from None


def publish_post(post: PostInput, opts: FlowOptions, storage_state: dict[str, Any], log: list[str]) -> FlowResult:
    """브라우저를 띄워 run_flow 를 돌린다. 실패하면 스크린샷·DOM 을 남기고 PublishError 로 바꾼다."""
    from playwright.sync_api import sync_playwright

    progress = Progress()
    with sync_playwright() as pw:
        browser = launch_browser(pw, headless=opts.headless, executable_path=opts.executable_path)
        try:
            context = browser.new_context(storage_state=storage_state, locale="ko-KR", viewport=VIEWPORT)
            page = context.new_page()
            page.set_default_timeout(opts.step_timeout_ms)
            page.set_default_navigation_timeout(opts.timeout_ms)
            page.on("dialog", lambda d: _on_dialog(d, log))
            try:
                return run_flow(page, post, opts, log, progress)
            except SessionExpired:
                raise
            except Exception as e:  # noqa: BLE001 — Playwright 오류·흐름 오류 모두 화면을 남기고 PublishError 로
                shots = capture(page, post.asset_id, opts.log_dir)
                where = ", ".join(str(p.relative_to(paths.ROOT)) if p.is_relative_to(paths.ROOT) else str(p) for p in shots)
                retryable = getattr(e, "retryable", True) and not progress.submitted
                head = str(e).strip().splitlines()[0][:300] if str(e).strip() else type(e).__name__
                msg = f"티스토리 [{progress.step}] 실패: {head}"
                if progress.submitted:
                    msg += (" — 발행(저장) 버튼을 누른 뒤라 자동 재시도하지 않습니다. 블로그에서 글이 올라갔는지 확인하고, "
                            "올라갔으면 `publish mark --platform tistory --remote-id <글 주소>`, 아니면 `publish retry` 하세요.")
                if where:
                    msg += f" (스크린샷·DOM: {where})"
                raise PublishError(msg, retryable=retryable) from None
        finally:
            browser.close()


# ---------------------------------------------------------------- 발행기

class TistoryPublisher:
    platform = "tistory"
    kinds = ("blog",)

    def __init__(self, **overrides: Any):
        """overrides: FlowOptions 필드 덮어쓰기(테스트에서 newpost_url·selectors·대기 시간 주입)."""
        self.overrides = overrides

    def publish(self, ctx: PublishContext) -> PublishResult:
        rid = ctx.target.remote_id
        if rid and not rid.startswith(DRYRUN_PREFIX):
            # 티스토리는 처리 대기 단계가 없다 — 원격 ID 가 있으면 이미 올라간 글이다(새로 올리려면 retry --reupload).
            return PublishResult("published", remote_id=rid, url=ctx.target.url, note="이미 발행됨(다시 올리지 않음)")
        opts = flow_options(ctx.cfg, **self.overrides)
        post = build_post_input(ctx)
        if _is_dry(ctx):
            return self._dry_run(ctx, post, opts)
        state = load_storage_state()
        res = publish_post(post, opts, state, ctx.log)
        note = "; ".join(res.warnings)
        if res.mode == "draft":
            return PublishResult(
                "skipped",
                note=("임시저장까지 완료 — 티스토리 글 관리 > 임시저장에서 확인 후 발행하고 "
                      f"`python -m studio publish mark --week {ctx.week} --asset {post.asset_id} --platform tistory --remote-id <글 주소>` "
                      "로 연결하세요." + (f" {note}" if note else "")),
                extra={"mode": "draft", "warnings": res.warnings},
            )
        return PublishResult("published", remote_id=res.remote_id or res.url, url=res.url, note=note,
                             extra={"source": post.source, "warnings": res.warnings})

    def check(self, ctx: PublishContext) -> PublishResult:
        """티스토리는 처리 대기 상태가 없다. 기록된 주소가 있으면 published."""
        if ctx.target.url or ctx.target.remote_id:
            return PublishResult("published", remote_id=ctx.target.remote_id, url=ctx.target.url)
        raise PublishError(
            f"{ctx.asset.asset_id}: 확인할 티스토리 글 주소가 없습니다. 블로그에서 확인 후 `publish mark --platform tistory --remote-id <글 주소>`.",
            retryable=False,
        )

    def _dry_run(self, ctx: PublishContext, post: PostInput, opts: FlowOptions) -> PublishResult:
        try:
            load_storage_state()
            session = "있음"
        except PublishError:
            session = "없음(실제 발행 전 `auth tistory` 필요)"
        log = ctx.log
        log.append("[dry-run] 브라우저를 띄우지 않습니다.")
        log.append(f"[dry-run] 세션: {session}")
        log.append(f"[dry-run] 열기 {opts.newpost_url} → HTML 모드 전환")
        log.append(f"[dry-run] 제목 {post.title!r}")
        log.append(f"[dry-run] 본문 HTML {len(post.html)}자 ({post.source})")
        log.append(f"[dry-run] 태그 {post.tags}")
        log.append(f"[dry-run] 카테고리 {opts.category or '(없음)'} · 공개 설정 {opts.visibility}")
        log.append(f"[dry-run] {'임시저장' if opts.mode == 'draft' else '발행'} 버튼")
        if opts.mode == "draft":
            return PublishResult("skipped", note="dry-run: 임시저장 모드", extra={"mode": "draft"})
        return PublishResult("published", remote_id=f"{DRYRUN_PREFIX}tistory-{post.asset_id}", note="dry-run: 요청을 보내지 않았습니다")


# ---------------------------------------------------------------- auth tistory

def _headless_env() -> bool:
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return True
    return sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _logged_in(page, context) -> bool:
    url = page.url
    if is_login_url(url):
        return False
    try:
        if any(c.get("name") in SESSION_COOKIES and str(c.get("domain", "")).endswith("tistory.com") for c in context.cookies()):
            return True
    except Exception:  # noqa: BLE001
        pass
    host = re.sub(r"^https?://", "", url).split("/")[0]
    return host.endswith("tistory.com")


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def save_state(state: dict[str, Any], state_file: Path, b64_file: Path) -> None:
    text = json.dumps(state, ensure_ascii=False)
    _write_private(state_file, text)
    _write_private(b64_file, base64.b64encode(text.encode("utf-8")).decode("ascii"))


def check_session(cfg: Config, log: Callable[[str], None] = print) -> bool:
    """저장된 세션으로 글쓰기 화면이 열리는지(헤드리스) 확인한다."""
    from playwright.sync_api import sync_playwright

    opts = flow_options(cfg)
    state = load_storage_state()
    with sync_playwright() as pw:
        browser = launch_browser(pw, headless=True, executable_path=opts.executable_path)
        try:
            page = browser.new_context(storage_state=state, locale="ko-KR").new_page()
            page.on("dialog", lambda d: d.dismiss())
            page.goto(opts.newpost_url, wait_until="domcontentloaded", timeout=opts.timeout_ms)
            _wait_editor(page, opts)
            return True
        except SessionExpired as e:
            log(str(e))
            return False
        finally:
            browser.close()


def cmd_auth(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.check:
        try:
            ok = check_session(cfg)
        except PublishError as e:
            print(f"[오류] {e}")
            return 1
        print("티스토리 세션 정상 — 글쓰기 화면이 열립니다." if ok else "티스토리 세션이 만료됐습니다.")
        return 0 if ok else 1
    if _headless_env():
        print("화면(디스플레이)이 없는 환경입니다. `auth tistory` 는 브라우저 창에서 직접 카카오 로그인을 해야 하므로 "
              "개인 컴퓨터(맥·윈도·데스크톱 리눅스)에서 실행하세요. 만들어진 .secrets/tistory_state.b64 내용을 "
              f"GitHub Secret {ENV_STATE_B64} 로 넣으면 CI 가 그 세션을 씁니다.")
        return 2
    from playwright.sync_api import sync_playwright

    state_file = Path(args.state) if args.state else state_path()
    b64_file = Path(args.out) if args.out else paths.SECRETS / STATE_B64_FILE
    print(f"브라우저 창에서 티스토리(카카오계정)에 로그인하세요. 2단계 인증까지 마치면 자동으로 저장합니다(제한 {args.timeout}초).")
    with sync_playwright() as pw:
        browser = launch_browser(pw, headless=False)
        try:
            context = browser.new_context(locale="ko-KR", viewport=VIEWPORT)
            page = context.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            deadline = time.monotonic() + args.timeout
            while not _logged_in(page, context):
                if time.monotonic() >= deadline:
                    print("시간 안에 로그인을 확인하지 못했습니다. 다시 실행하세요(--timeout 으로 늘릴 수 있음).")
                    return 1
                page.wait_for_timeout(1000)
            page.wait_for_timeout(2000)   # 로그인 직후 쿠키가 모두 쓰일 때까지
            state = context.storage_state()
        finally:
            browser.close()
    save_state(state, state_file, b64_file)
    print(f"세션 저장: {state_file} (권한 600, 커밋 금지)")
    print(f"GitHub Secret 용 base64: {b64_file}")
    print(f"  등록 예) gh secret set {ENV_STATE_B64} < {b64_file}")
    print("세션이 만료되면(로그인 페이지로 튕김) 이 명령을 다시 실행하세요. 확인만 하려면 `auth tistory --check`.")
    return 0


def register_auth(g: argparse._SubParsersAction) -> None:
    p = g.add_parser("tistory", help="티스토리 로그인 세션 저장(브라우저 창에서 카카오 로그인)")
    p.add_argument("--out", help=f"GitHub Secret 용 base64 파일(기본 .secrets/{STATE_B64_FILE})")
    p.add_argument("--state", help=f"storageState JSON 파일(기본 .secrets/{STATE_FILE})")
    p.add_argument("--timeout", type=int, default=300, help="로그인 대기 시간(초)")
    p.add_argument("--check", action="store_true", help="저장된 세션으로 글쓰기 화면이 열리는지만 확인(헤드리스)")
    p.set_defaults(func=cmd_auth)
