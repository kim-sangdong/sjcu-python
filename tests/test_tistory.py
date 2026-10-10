"""티스토리 발행기 테스트 — tistory.com 대신 file:// 가짜 글쓰기 페이지로 Playwright 흐름의 골격을 확인한다."""

from __future__ import annotations

import argparse
import base64
import json
from datetime import datetime
from pathlib import Path

import pytest

from studio import config, paths
from studio.config import Config
from studio.models import BlogPost, PublishTarget, RenderedAsset
from studio.publish import tistory as T
from studio.publish.base import PublishContext, PublishError

WEEK = "2026-W42"
AT = datetime(2026, 10, 12, 8, 0, tzinfo=paths.KST)
TITLE = "복습 간격 계획표 만드는 법: 달력 한 장으로"
BODY = '<h2 style="color:#8C2129">소제목</h2>\n<p>본문 &amp; 표</p>\n<table><tr><td>1</td></tr></table>'
FAST = dict(step_delay_ms=0, type_delay_ms=0, step_timeout_ms=2500, timeout_ms=4000)

EDITOR = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>글쓰기</title><style>.hidden{display:none}</style></head>
<body>
<button id="category-btn" type="button" onclick="toggle('category-list')">카테고리</button>
<div id="category-list" class="hidden">
  <div class="mce-menu-item" role="option" onclick="pickCat(this)">공부법</div>
  <div class="mce-menu-item" role="option" onclick="pickCat(this)">- 시험 정보</div>
</div>
<button id="editor-mode-layer-btn-open" type="button" onclick="toggle('mode-layer')">기본모드</button>
<div id="mode-layer" class="hidden"><button id="editor-mode-html" type="button" onclick="toHtml()">HTML</button></div>
<textarea id="post-title-inp" placeholder="제목을 입력하세요"></textarea>
<div id="basic-area"><div contenteditable="true">기본 편집기</div></div>
<div id="html-area" class="hidden">__EDITOR__</div>
<input id="tagText" placeholder="태그입력">
<button id="publish-layer-btn" type="button" onclick="toggle('publish-layer')">완료</button>
<button id="draft-btn" type="button" onclick="saveDraft()">임시저장</button>
<div id="toast" class="hidden"></div>
<div id="publish-layer" class="hidden">
  <input type="radio" name="vis" id="open20" value="20"><label for="open20">공개</label>
  <input type="radio" name="vis" id="open15" value="15"><label for="open15">보호</label>
  <input type="radio" name="vis" id="open0" value="0" checked><label for="open0">비공개</label>
  __PUBLISH__
</div>
<script>
const state = {mode: 'basic', category: '', tags: [], resumed: '0'};
if (confirm('임시저장된 글이 있습니다. 이어서 작성하시겠습니까?')) { state.resumed = '1'; }
function toggle(id) { document.getElementById(id).classList.toggle('hidden'); }
function pickCat(el) { state.category = el.textContent.replace(/^-\\s*/, '').trim(); toggle('category-list'); }
function toHtml() {
  if (confirm('HTML 모드로 전환하면 서식이 바뀔 수 있습니다. 전환하시겠습니까?')) {
    state.mode = 'html';
    document.getElementById('basic-area').classList.add('hidden');
    document.getElementById('html-area').classList.remove('hidden');
    toggle('mode-layer');
  }
}
document.getElementById('tagText').addEventListener('keydown', e => {
  if (e.key === 'Enter') { const v = e.target.value.trim(); if (v) state.tags.push(v); e.target.value = ''; }
});
__SCRIPT__
function saveDraft() {
  const t = document.getElementById('toast'); t.textContent = '저장되었습니다'; t.classList.remove('hidden');
  document.title = 'draft:' + document.getElementById('post-title-inp').value + ':' + bodyValue().length;
}
function doPublish() {
  const q = new URLSearchParams({
    title: document.getElementById('post-title-inp').value, body: bodyValue(), tags: state.tags.join('|'),
    category: state.category, vis: document.querySelector('input[name=vis]:checked').value, mode: state.mode, resumed: state.resumed});
  location.href = 'posts.html?' + q.toString();
}
</script></body></html>
"""
TEXTAREA = ('<textarea id="html-editor-textarea"></textarea>',
            "function bodyValue() { return document.getElementById('html-editor-textarea').value; }")
CODEMIRROR = ('<div class="CodeMirror" style="min-height:40px;border:1px solid #999"></div>',
              "const cmEl = document.querySelector('.CodeMirror');\n"
              "cmEl.CodeMirror = {v: '', setValue(x) { this.v = x; }, getValue() { return this.v; }};\n"
              "function bodyValue() { return cmEl.CodeMirror.getValue(); }")
PUBLISH_OK = '<button id="publish-btn" type="button" onclick="doPublish()">공개 발행</button>'
PUBLISH_NOOP = '<button id="publish-btn" type="button">공개 발행</button>'
POSTS = """<!doctype html><html><head><meta charset="utf-8"><title>글 관리</title></head><body>
<ul id="list"></ul><pre id="received"></pre>
<script>
const p = new URLSearchParams(location.search);
const a = document.createElement('a'); a.href = 'https://testblog.tistory.com/123'; a.textContent = p.get('title');
document.getElementById('list').appendChild(document.createElement('li')).appendChild(a);
document.getElementById('received').textContent = JSON.stringify(Object.fromEntries(p.entries()));
</script></body></html>"""
LOGIN = "<!doctype html><html><head><meta charset='utf-8'></head><body><button>카카오계정으로 로그인</button></body></html>"


def site(tmp: Path, editor=TEXTAREA, publish=PUBLISH_OK) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    html = EDITOR.replace("__EDITOR__", editor[0]).replace("__SCRIPT__", editor[1]).replace("__PUBLISH__", publish)
    (tmp / "newpost.html").write_text(html, encoding="utf-8")
    (tmp / "posts.html").write_text(POSTS, encoding="utf-8")
    (tmp / "auth").mkdir(exist_ok=True)
    (tmp / "auth" / "login.html").write_text(LOGIN, encoding="utf-8")
    return tmp / "newpost.html"


def opts(page_file: Path, tmp: Path, **kw) -> T.FlowOptions:
    base = dict(blog_name="testblog", newpost_url=page_file.as_uri(), category="공부법", visibility="public",
                log_dir=tmp / "logs", **FAST)
    base.update(kw)
    return T.FlowOptions(**base)


POST = T.PostInput(asset_id="2026W42-B01", title=TITLE, html=BODY, tags=["복습", "공부법", "간격 효과"])
STATE = {"cookies": [], "origins": []}


@pytest.fixture
def page():
    # sync Playwright 는 한 스레드에 하나만 돌 수 있다 → publish_post(자체 실행) 테스트와 섞이도록 테스트마다 연다.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = T.launch_browser(pw, headless=True)
        pg = b.new_context().new_page()
        log: list[str] = []
        pg.on("dialog", lambda d: T._on_dialog(d, log))
        pg.flow_log = log
        yield pg
        b.close()


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_ROOT", str(tmp_path))
    monkeypatch.delenv("STUDIO_DRY_RUN", raising=False)
    monkeypatch.delenv(T.ENV_STATE_B64, raising=False)
    for name, sub in [("ROOT", ""), ("CONTENT", "content"), ("OUT", "out"), ("SECRETS", ".secrets")]:
        monkeypatch.setattr(paths, name, tmp_path / sub if sub else tmp_path)
    config.load_config.cache_clear()
    yield tmp_path
    config.load_config.cache_clear()


# ---------------------------------------------------------------- 가짜 페이지 흐름

def test_flow_textarea_publish(page, tmp_path):
    o = opts(site(tmp_path / "site"), tmp_path)
    res = T.run_flow(page, POST, o, page.flow_log)
    got = json.loads(page.locator("#received").inner_text())
    assert got == {"title": TITLE, "body": BODY, "tags": "복습|공부법|간격 효과", "category": "공부법",
                   "vis": "20", "mode": "html", "resumed": "0"}   # 이어쓰기 확인창은 거절, 모드 전환은 수락
    assert (res.mode, res.url, res.remote_id, res.warnings) == ("publish", "https://testblog.tistory.com/123", "123", [])
    assert any("HTML 모드 전환" in line for line in page.flow_log)


def test_flow_codemirror_private_subcategory(page, tmp_path):
    o = opts(site(tmp_path / "site", editor=CODEMIRROR), tmp_path, category="시험 정보", visibility="private")
    T.run_flow(page, POST, o, page.flow_log)
    got = json.loads(page.locator("#received").inner_text())
    assert (got["body"], got["category"], got["vis"]) == (BODY, "시험 정보", "0")
    assert any("CodeMirror" in line for line in page.flow_log)


def test_flow_unknown_category_warns_but_publishes(page, tmp_path):
    o = opts(site(tmp_path / "site"), tmp_path, category="없는 카테고리", step_timeout_ms=600)
    res = T.run_flow(page, POST, o, page.flow_log)
    assert res.url and "카테고리 '없는 카테고리'" in res.warnings[0]
    assert json.loads(page.locator("#received").inner_text())["category"] == ""


def test_flow_draft_mode(page, tmp_path):
    o = opts(site(tmp_path / "site"), tmp_path, mode="draft")
    res = T.run_flow(page, POST, o, page.flow_log)
    assert res.mode == "draft" and res.url is None and res.warnings == []
    assert page.title() == f"draft:{TITLE}:{len(BODY)}"


def test_flow_text_fallback_selectors(page, tmp_path):
    """id 가 바뀌어도 텍스트 기반 후보(button:has-text)로 찾는다."""
    o = opts(site(tmp_path / "site"), tmp_path,
             selectors=T.Selectors(mode_menu=("#없는-id", "button:has-text('기본모드')"),
                                   complete=("#없는-id", "button:has-text('완료')"),
                                   publish=("#없는-id", "button:has-text('공개 발행')")))
    res = T.run_flow(page, POST, o, page.flow_log)
    assert res.remote_id == "123"


def test_flow_login_redirect_is_session_expired(page, tmp_path):
    site(tmp_path / "site")
    o = opts(tmp_path / "site" / "auth" / "login.html", tmp_path)
    with pytest.raises(T.SessionExpired) as ei:
        T.run_flow(page, POST, o, page.flow_log)
    assert ei.value.retryable is False and "auth tistory" in str(ei.value)


# ---------------------------------------------------------------- 브라우저 수명·실패 기록

def test_publish_post_missing_button_saves_artifacts(tmp_path):
    o = opts(site(tmp_path / "site", publish=""), tmp_path, step_timeout_ms=800)
    with pytest.raises(PublishError) as ei:
        T.publish_post(POST, o, STATE, [])
    e = ei.value
    assert e.retryable is True                                   # 발행 버튼을 누르기 전 → 재시도해도 중복 없음
    assert "[발행]" in str(e) and "SEL_" in str(e)
    shots = sorted((tmp_path / "logs").glob("tistory-2026W42-B01-*"))
    assert [p.suffix for p in shots] == [".html", ".png"]
    assert "post-title-inp" in shots[0].read_text(encoding="utf-8")


def test_publish_post_no_result_after_click_is_not_retryable(tmp_path):
    o = opts(site(tmp_path / "site", publish=PUBLISH_NOOP), tmp_path, timeout_ms=1200)
    with pytest.raises(PublishError) as ei:
        T.publish_post(POST, o, STATE, [])
    assert ei.value.retryable is False and "publish mark" in str(ei.value)


# ---------------------------------------------------------------- 발행기

def _cfg(**tistory) -> Config:
    return Config.model_validate({"platforms": {"tistory": {"blog_name": "testblog", "category": "공부법", **tistory}}})


def _ctx(root: Path, cfg: Config, *, html: bool = True, dry_run: bool = False, **target_kw) -> PublishContext:
    d = root / "out" / "weeks" / WEEK / "2026W42-B01"
    d.mkdir(parents=True, exist_ok=True)
    if html:
        (d / "post.html").write_text(BODY, encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"title": TITLE, "tags": ["#복습", "공부법", "복습", "간격, 효과"]}, ensure_ascii=False), encoding="utf-8")
    rel = f"out/weeks/{WEEK}/2026W42-B01"
    asset = RenderedAsset(asset_id="2026W42-B01", kind="blog", files={"html": f"{rel}/post.html", "meta": f"{rel}/meta.json"})
    post = BlogPost(id="2026W42-B01", week=WEEK, title="원고 제목", keyword="복습", tags=["원고태그"], body_markdown="## 소제목\n\n본문")
    target = PublishTarget(asset_id="2026W42-B01", asset_kind="blog", platform="tistory", publish_at=AT, **target_kw)
    return PublishContext(week=WEEK, target=target, asset=asset, content=post, cfg=cfg, dry_run=dry_run)


def _set_state_env(monkeypatch, state=STATE):
    monkeypatch.setenv(T.ENV_STATE_B64, base64.b64encode(json.dumps(state).encode()).decode())


def test_publisher_end_to_end_with_fake_page(root, tmp_path, monkeypatch):
    _set_state_env(monkeypatch)
    page_file = site(tmp_path / "site")
    pub = T.TistoryPublisher(newpost_url=page_file.as_uri(), log_dir=tmp_path / "logs", **FAST)
    ctx = _ctx(root, _cfg())
    res = pub.publish(ctx)
    assert (res.status, res.remote_id, res.url) == ("published", "123", "https://testblog.tistory.com/123")
    assert res.extra["source"] == "post.html"
    assert any("글 주소" in line for line in ctx.log)


def test_publisher_draft_mode_returns_skipped(root, tmp_path, monkeypatch):
    _set_state_env(monkeypatch)
    pub = T.TistoryPublisher(newpost_url=site(tmp_path / "site").as_uri(), log_dir=tmp_path / "logs", **FAST)
    res = pub.publish(_ctx(root, _cfg(mode="draft")))
    assert res.status == "skipped" and "publish mark" in res.note


def test_publisher_dry_run_does_not_launch_browser(root, monkeypatch):
    monkeypatch.setattr(T, "publish_post", lambda *a, **k: pytest.fail("dry-run 에서 브라우저를 띄우면 안 됨"))
    ctx = _ctx(root, _cfg(visibility="protected"), dry_run=True)
    res = T.TistoryPublisher().publish(ctx)
    assert res.status == "published" and res.remote_id == "dryrun-tistory-2026W42-B01"
    text = "\n".join(ctx.log)
    assert "브라우저를 띄우지 않습니다" in text and "세션: 없음" in text
    assert "https://testblog.tistory.com/manage/newpost/" in text and "공개 설정 protected" in text
    assert "['복습', '공부법', '간격 효과']" in text      # meta.json 태그 정리(#·쉼표·중복)


def test_publisher_env_dry_run(root, monkeypatch):
    monkeypatch.setenv("STUDIO_DRY_RUN", "1")
    monkeypatch.setattr(T, "publish_post", lambda *a, **k: pytest.fail("브라우저 금지"))
    assert T.TistoryPublisher().publish(_ctx(root, _cfg())).remote_id.startswith("dryrun-")


def test_publisher_requires_session(root):
    with pytest.raises(PublishError) as ei:
        T.TistoryPublisher().publish(_ctx(root, _cfg()))
    assert ei.value.retryable is False and "auth tistory" in str(ei.value)


def test_publisher_does_not_repost(root, monkeypatch):
    monkeypatch.setattr(T, "publish_post", lambda *a, **k: pytest.fail("중복 발행 금지"))
    ctx = _ctx(root, _cfg(), remote_id="123", url="https://testblog.tistory.com/123")
    assert T.TistoryPublisher().publish(ctx).status == "published"


def test_body_rerendered_when_post_html_missing(root, monkeypatch):
    from studio.blog import render

    monkeypatch.setattr(render, "build_html", lambda post, cfg: ("<p>다시 만든 본문</p>", {"video_embed": None}, []))
    item = T.build_post_input(_ctx(root, _cfg(), html=False))
    assert item.html == "<p>다시 만든 본문</p>" and item.source.startswith("원고에서 다시 렌더")
    assert item.title == TITLE                                   # 제목은 meta.json 우선


def test_body_rerendered_for_new_video_embed(root, monkeypatch):
    from studio.blog import render

    monkeypatch.setattr(render, "build_html", lambda post, cfg: ("<iframe></iframe>", {"video_embed": "AbCdEfGhIjK"}, []))
    ctx = _ctx(root, _cfg())
    ctx.content = ctx.content.model_copy(update={"related_video_id": "2026W42-S01"})
    item = T.build_post_input(ctx)
    assert item.html == "<iframe></iframe>" and "임베드" in item.source


def test_check_returns_recorded_url(root):
    ctx = _ctx(root, _cfg(), remote_id="123", url="https://testblog.tistory.com/123", status="processing")
    assert T.TistoryPublisher().check(ctx).status == "published"
    with pytest.raises(PublishError):
        T.TistoryPublisher().check(_ctx(root, _cfg()))


# ---------------------------------------------------------------- 설정·세션

def test_flow_options_from_config():
    o = T.flow_options(_cfg(blog_name="https://TestBlog.tistory.com/", mode="draft", visibility="protected", timeout_seconds=5))
    assert (o.blog_name, o.newpost_url, o.mode, o.visibility, o.timeout_ms) == (
        "testblog", "https://testblog.tistory.com/manage/newpost/", "draft", "protected", 5000)
    with pytest.raises(PublishError):
        T.flow_options(_cfg(blog_name=""))
    with pytest.raises(PublishError):
        T.flow_options(_cfg(mode="auto"))


def test_load_storage_state_sources(root, monkeypatch):
    with pytest.raises(PublishError):
        T.load_storage_state()
    T.state_path().parent.mkdir(parents=True)
    T.state_path().write_text(json.dumps({"cookies": [{"name": "TSSESSION"}], "origins": []}))
    assert T.load_storage_state()["cookies"][0]["name"] == "TSSESSION"
    _set_state_env(monkeypatch, {"cookies": [], "origins": [{"origin": "x"}]})
    assert T.load_storage_state()["origins"] == [{"origin": "x"}]   # 환경변수가 우선
    monkeypatch.setenv(T.ENV_STATE_B64, "이건base64가아님")
    with pytest.raises(PublishError) as ei:
        T.load_storage_state()
    assert ei.value.retryable is False


def test_save_state_writes_private_files(root):
    T.save_state({"cookies": [], "origins": []}, root / ".secrets" / "s.json", root / ".secrets" / "s.b64")
    assert (root / ".secrets" / "s.json").stat().st_mode & 0o777 == 0o600
    raw = (root / ".secrets" / "s.b64").read_text()
    assert json.loads(base64.b64decode(raw)) == {"cookies": [], "origins": []}


def test_auth_command_in_headless_env(root, monkeypatch, capsys):
    monkeypatch.setenv("CI", "true")
    parser = argparse.ArgumentParser()
    g = parser.add_subparsers()
    T.register_auth(g)
    args = parser.parse_args(["tistory"])
    assert args.func(args) == 2
    assert T.ENV_STATE_B64 in capsys.readouterr().out


def test_post_id_and_login_detection():
    assert T.post_id_of("https://b.tistory.com/57") == "57"
    assert T.post_id_of("https://b.tistory.com/entry/제목") == "https://b.tistory.com/entry/제목"
    assert T.is_login_url("https://accounts.kakao.com/login?continue=x")
    assert not T.is_login_url("https://b.tistory.com/manage/newpost/")
    assert T.clean_tags(["#a", "a", "b, c", " "]) == ["a", "b c"]
