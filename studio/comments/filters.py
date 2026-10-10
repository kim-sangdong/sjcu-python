"""댓글 글자 규칙 — 받은 댓글의 스팸 의심 표시(flags)와 보낼 답글의 안전장치."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from studio.config import Config

MAX_REPLY_CHARS = 500
MAX_SAME_REPLY = 2            # 같은 문구는 최근 기간 안에 2번까지만(3번째부터 거부)
SAME_REPLY_WINDOW_DAYS = 30

# 광고·사기성 댓글에 흔한 표현(채널 설정 rules.spam_phrases 로 더할 수 있다)
SPAM_PHRASES = [
    "텔레그램", "telegram", "whatsapp", "왓츠앱", "카톡방", "오픈채팅", "오픈톡", "리딩방", "무료리딩",
    "수익보장", "고수익", "재택알바", "재택부업", "부업문의", "투자문의", "코인추천", "주식추천",
    "구독자늘려", "조회수늘려", "홍보문의", "광고문의", "dm주세요", "디엠주세요", "프로필링크",
]

# 링크로 볼 최상위 도메인(오탐을 줄이려고 목록으로 제한)
_TLDS = (
    "com|net|org|kr|co|io|ly|me|app|link|gl|be|tv|shop|site|xyz|info|biz|us|to|gg|cc|page|dev|ai|so|im|in|"
    "jp|cn|ru|top|club|online|store|live|life|world|pro|one|vip|fun|ink|cafe|blog|news|click|win|bet|kim"
)
_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+")
_DOMAIN_RE = re.compile(rf"(?i)(?<![a-z0-9@._-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:{_TLDS}))(?![a-z0-9-])(?::\d+)?(?:/[^\s]*)?")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<!\d)(?:01[016789]|0\d{1,2})[-\s.]?\d{3,4}[-\s.]?\d{4}(?!\d)")
_REPEAT_CHAR_RE = re.compile(r"(.)\1{9,}")                    # 같은 글자 10번 이상
_REPEAT_WORD_RE = re.compile(r"(?<!\S)(\S{2,})(?:\s+\1){3,}(?!\S)")   # 같은 낱말 4번 이상 띄어 반복
_BENIGN_REPEAT = set("ㅋㅎㅠㅜ.!?~^ㅡ-ㄷㄱ")                    # 웃음·감탄 반복은 스팸으로 보지 않는다
_QUESTION_RE = re.compile(r"\?|까요|나요|가요|뭔가요|뭐예요|뭐에요|어떻게|왜요|궁금|알려\s*주")


def norm(text: str) -> str:
    """비교용: 소문자 + 공백 제거."""
    return re.sub(r"\s+", "", text.lower())


def reply_key(text: str) -> str:
    """같은 문구 판정용: 공백·문장부호를 지운 소문자."""
    return re.sub(r"[\W_]+", "", text.lower())


def find_hosts(text: str) -> list[str]:
    """글 안의 링크(주소·도메인)의 호스트 목록. 이메일은 제외한다."""
    stripped = _EMAIL_RE.sub(" ", text)
    hosts: list[str] = []
    for m in _URL_RE.finditer(stripped):
        raw = m.group(0)
        host = urlparse(raw if "://" in raw else "http://" + raw).hostname or ""
        hosts.append(host.lower())
        stripped = stripped.replace(raw, " ")
    for m in _DOMAIN_RE.finditer(stripped):
        hosts.append(m.group(1).lower())
    return [h for h in hosts if h]


def allowed_host(cfg: Config) -> str | None:
    """답글에 허용하는 유일한 링크 호스트(앱 추적 링크 cfg.app.link_base). 설정이 없으면 링크 전부 금지."""
    base = (cfg.app.link_base or "").strip()
    if not base:
        return None
    return (urlparse(base).hostname or "").lower() or None


def spam_phrases(cfg: Config) -> list[str]:
    extra = cfg.rules.model_extra.get("spam_phrases", []) if cfg.rules.model_extra else []
    return SPAM_PHRASES + [str(p) for p in (extra or [])]


def phrase_hits(text: str, phrases: list[str]) -> list[str]:
    t = norm(text)
    return [p for p in phrases if p and norm(p) in t]


def comment_flags(text: str, cfg: Config) -> list[str]:
    """받은 댓글 표시. 'spam' 이 있으면 답하지 않는 것이 기본(스킬 규칙)."""
    flags: list[str] = []
    if find_hosts(text):
        flags.append("link")
    rep = _REPEAT_CHAR_RE.search(text)
    heavy_repeat = bool(rep and rep.group(1) not in _BENIGN_REPEAT and not rep.group(1).isspace())
    wrep = _REPEAT_WORD_RE.search(text)
    word_repeat = bool(wrep and not set(wrep.group(1)) <= _BENIGN_REPEAT)
    if rep or wrep:
        flags.append("repeat")
    if phrase_hits(text, spam_phrases(cfg)):
        flags.append("spam_phrase")
    if phrase_hits(text, cfg.rules.banned_phrases):
        flags.append("banned_phrase")     # 답글에서 이 표현을 따라 쓰지 말 것
    if _EMAIL_RE.search(text) or _PHONE_RE.search(text):
        flags.append("personal_info")
    if _QUESTION_RE.search(text):
        flags.append("question")
    if "link" in flags or "spam_phrase" in flags or heavy_repeat or word_repeat:
        flags.insert(0, "spam")
    return flags


def reply_problems(reply: str, cfg: Config, max_chars: int = MAX_REPLY_CHARS) -> list[str]:
    """보낼 답글의 문제 목록(비어 있으면 통과). 같은 문구 반복은 post 단계에서 따로 센다."""
    problems: list[str] = []
    if not reply.strip():
        return ["답글이 비어 있습니다"]
    if len(reply) > max_chars:
        problems.append(f"답글이 {len(reply)}자입니다(최대 {max_chars}자)")
    for p in phrase_hits(reply, cfg.rules.banned_phrases):
        problems.append(f"금지 표현 '{p}'")
    ok_host = allowed_host(cfg)
    for h in find_hosts(reply):
        if h != ok_host:
            problems.append(f"외부 링크 '{h}' (허용: {ok_host or '없음 — 답글에 링크를 넣지 않습니다'})")
    if _EMAIL_RE.search(reply) or _PHONE_RE.search(reply):
        problems.append("이메일·전화번호가 들어 있습니다")
    return problems
