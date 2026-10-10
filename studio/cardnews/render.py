"""카드뉴스 렌더: content/weeks/<week>/cardnews/<id>.json → out/weeks/<week>/<id>/slideNN.png + slideNN.jpg.

인스타 캐러셀은 JPEG 만 받으므로(facts-meta-tiktok-tistory.md 1.4) manifest 의 files 에는 JPEG 를 기록한다.
  files = {"slide01": "out/weeks/.../slide01.jpg", ...}
  meta  = {"slide_count": n, "png": [...], "alt_texts": [...], "layouts": {...}, "warnings": [...]}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from studio import paths
from studio.cardnews.canvas import brand_of, save_png_jpeg
from studio.cardnews.layouts import CIRCLED, LAYOUTS, H, W, CardRenderError, Slide, cta_texts, draw_slide, list_items, slide_variant
from studio.cardnews.text import NBSP, TextOverflow, plain
from studio.config import Config
from studio.manifest import upsert_asset
from studio.models import CardNews, RenderedAsset, read_model

MAX_JPEG_BYTES = 8 * 1024 * 1024   # 인스타 이미지 상한 [기억·미확인]
KIND_KO = {"cover": "표지", "body": "본문", "list": "목록", "quiz": "퀴즈", "answer": "정답", "cta": "안내"}


@dataclass
class CardResult:
    card_id: str
    jpgs: list[Path]
    pngs: list[Path]
    alt_texts: list[str]
    layouts: dict[str, int]
    warnings: list[str] = field(default_factory=list)


def card_out_dir(week: str, card_id: str) -> Path:
    return paths.out_dir(week) / card_id


def rel(p: Path) -> str:
    """ROOT 기준 상대경로(manifest 기록용)."""
    try:
        return p.resolve().relative_to(paths.ROOT.resolve()).as_posix()
    except ValueError:
        return str(p)


def _texts(cfg: Config) -> dict[str, str]:
    """channel.yaml 의 선택 섹션 cardnews: {swipe_hint, answer_hint, cta_button, quiz_label, answer_label}."""
    extra = (cfg.model_extra or {}).get("cardnews") or {}
    return {k: str(v) for k, v in extra.items() if isinstance(v, (str, int, float))}


def answer_index(answer: Any, choices: list[str], where: str) -> int:
    """params.answer(1부터 번호, '③', 선택지 글) → 0부터 번호."""
    n = len(choices)
    idx: int | None = None
    if isinstance(answer, bool):
        idx = None
    elif isinstance(answer, int):
        idx = answer - 1
    elif isinstance(answer, str):
        a = answer.strip()
        if a.isdigit():
            idx = int(a) - 1
        elif len(a) == 1 and a in CIRCLED:
            idx = CIRCLED.index(a)
        elif a in [c.strip() for c in choices]:
            idx = [c.strip() for c in choices].index(a)
    if idx is None or not 0 <= idx < n:
        raise CardRenderError(f"{where}: params.answer={answer!r} 가 선택지 {n}개 중 하나가 아닙니다. 1~{n} 번호로 쓰세요.")
    return idx


def _choices(params: dict[str, Any], where: str) -> list[str]:
    ch = params.get("choices")
    if not isinstance(ch, list) or not all(isinstance(c, (str, int, float)) for c in ch):
        raise CardRenderError(f"{where}: params.choices 에 선택지 글 목록(2~5개)을 넣으세요.")
    ch = [str(c).strip() for c in ch]
    if not 2 <= len(ch) <= 5 or not all(ch):
        raise CardRenderError(f"{where}: 선택지는 빈칸 없이 2~5개여야 합니다(지금 {len(ch)}개).")
    return ch


def build_slides(card: CardNews, cfg: Config) -> list[Slide]:
    brand = brand_of(cfg)
    texts = _texts(cfg)
    logo = paths.ROOT / cfg.brand.logo if cfg.brand.logo else None
    if logo and not logo.exists():
        logo = None
    total = len(card.slides)
    out: list[Slide] = []
    seq = 0
    last_quiz: tuple[list[str], int] | None = None
    for i, cs in enumerate(card.slides):
        where = f"슬라이드 {i + 1}/{total}({cs.kind})"
        choices: list[str] = []
        answer: int | None = None
        if cs.kind == "quiz":
            if not cs.title.strip():
                raise CardRenderError(f"{where}: 문제를 title 에 쓰세요.")
            choices = _choices(cs.params, where)
            if "answer" not in cs.params:
                raise CardRenderError(f"{where}: params.answer(정답 번호, 1부터)가 없습니다.")
            answer = answer_index(cs.params["answer"], choices, where)
            last_quiz = (choices, answer)
        elif cs.kind == "answer":
            if "choices" in cs.params:
                choices = _choices(cs.params, where)
            elif last_quiz:
                choices = last_quiz[0]
            if "answer" in cs.params:
                if not choices:
                    raise CardRenderError(f"{where}: params.answer 를 쓰려면 앞에 quiz 장이 있거나 params.choices 가 있어야 합니다.")
                answer = answer_index(cs.params["answer"], choices, where)
            elif last_quiz and choices is last_quiz[0]:
                answer = last_quiz[1]
        if cs.kind in ("body", "list"):
            seq += 1
        layout = cs.params.get("layout")
        n_var = len(LAYOUTS[cs.kind])
        variant = int(layout) % n_var if isinstance(layout, int) and not isinstance(layout, bool) else slide_variant(card.id, cs.kind, n_var)
        out.append(
            Slide(
                card_id=card.id,
                kind=cs.kind,
                title=cs.title,
                body=cs.body,
                params=cs.params,
                index=i + 1,
                total=total,
                seq=max(seq, 1),
                variant=variant,
                brand=brand,
                handle=cfg.channel.handle,
                app_name=cfg.app.name,
                next_kind=card.slides[i + 1].kind if i + 1 < total else None,
                choices=choices,
                answer=answer,
                texts=texts,
                logo=logo,
            )
        )
    return out


def alt_text(s: Slide) -> str:
    """인스타 캐러셀 항목 alt_text 용 설명."""
    parts = [f"{s.index}/{s.total}"]
    for t in (s.title, s.body):
        if t.strip():
            parts.append(" ".join(plain(t).split()))
    if s.kind == "list" and "items" in s.params:   # body 로 쓴 항목은 위에서 이미 들어갔다
        try:
            parts.append(" / ".join(plain(x) for x in list_items(s)))
        except CardRenderError:
            pass
    if s.kind == "quiz" and s.choices:
        parts.append(" ".join(f"{k + 1}) {plain(c)}" for k, c in enumerate(s.choices)))
    if s.kind == "answer" and s.answer is not None:
        parts.append(f"정답 {s.answer + 1}번" + (f": {plain(s.choices[s.answer])}" if s.choices else ""))
    if s.kind == "cta":
        parts.append(cta_texts(s)[2].replace(NBSP, " "))
    return " — ".join(parts)[:1000]


def _check_structure(card: CardNews) -> list[str]:
    warns = []
    kinds = [s.kind for s in card.slides]
    if kinds[-1] != "cta":
        warns.append("마지막 장이 cta 가 아닙니다 — 프로필 링크 안내(cta) 장을 마지막에 두세요.")
    if "cta" in kinds[:-1]:
        warns.append("cta 장이 마지막이 아닌 자리에 있습니다.")
    if kinds[0] != "cover":
        warns.append("첫 장이 cover 가 아닙니다 — 프로필 그리드·피드 미리보기는 첫 장이 대표가 됩니다.")
    for i, k in enumerate(kinds):
        if k == "answer" and "quiz" not in kinds[:i]:
            warns.append(f"슬라이드 {i + 1}: answer 장 앞에 quiz 장이 없습니다.")
    return warns


def render_card(card: CardNews, cfg: Config, out: Path | None = None) -> CardResult:
    """카드 한 벌을 그려 PNG·JPEG 로 저장한다. 글이 넘치거나 params 가 틀리면 CardRenderError."""
    out = out or card_out_dir(card.week, card.id)
    slides = build_slides(card, cfg)
    images = []
    removed: list[str] = []
    for s in slides:
        try:
            cv = draw_slide(s)
        except (TextOverflow, CardRenderError) as e:
            raise CardRenderError(f"{card.id} 슬라이드 {s.index}/{s.total}({s.kind}·{KIND_KO[s.kind]}) {e}") from e
        removed += cv.removed_chars
        img = cv.finish()
        assert img.size == (W, H)
        images.append(img)
    out.mkdir(parents=True, exist_ok=True)
    for old in list(out.glob("slide*.png")) + list(out.glob("slide*.jpg")):
        old.unlink()
    jpgs, pngs = [], []
    for i, img in enumerate(images, start=1):
        png, jpg = out / f"slide{i:02d}.png", out / f"slide{i:02d}.jpg"
        save_png_jpeg(img, png, jpg)
        if jpg.stat().st_size > MAX_JPEG_BYTES:
            raise CardRenderError(f"{card.id} 슬라이드 {i}: JPEG 가 8MB 를 넘습니다({jpg.stat().st_size // 1024}KB).")
        jpgs.append(jpg)
        pngs.append(png)
    warnings = _check_structure(card)
    if removed:
        uniq = "".join(dict.fromkeys(removed))
        warnings.append(f"글꼴에 없는 글자를 뺐습니다: {uniq!r} — 이모지 대신 ●·★·→·※ 같은 기호를 쓰세요.")
    layouts = {s.kind: s.variant for s in slides}
    return CardResult(card.id, jpgs, pngs, [alt_text(s) for s in slides], layouts, warnings)


def record(week: str, res: CardResult) -> RenderedAsset:
    asset = RenderedAsset(
        asset_id=res.card_id,
        kind="cardnews",
        files={f"slide{i:02d}": rel(p) for i, p in enumerate(res.jpgs, start=1)},
        meta={
            "slide_count": len(res.jpgs),
            "size": [W, H],
            "png": [rel(p) for p in res.pngs],
            "alt_texts": res.alt_texts,
            "layouts": res.layouts,
            "warnings": res.warnings,
        },
    )
    upsert_asset(week, asset)
    return asset


def card_sources(week: str) -> list[Path]:
    return sorted((paths.content_dir(week) / "cardnews").glob("*.json"))


def render_week(
    week: str, cfg: Config, only: list[str] | None = None, *, log: Callable[[str], None] = print
) -> tuple[list[CardResult], list[tuple[str, str]]]:
    """주차의 카드뉴스를 모두(또는 only 만) 렌더하고 manifest 에 기록한다. (성공 목록, [(ID, 오류)])."""
    done: list[CardResult] = []
    failed: list[tuple[str, str]] = []
    srcs = card_sources(week)
    if only:
        wanted = set(only)
        srcs = [p for p in srcs if p.stem in wanted]
        for missing in sorted(wanted - {p.stem for p in srcs}):
            failed.append((missing, f"원고가 없습니다: {rel(paths.content_dir(week) / 'cardnews' / (missing + '.json'))}"))
    for src in srcs:
        try:
            card = read_model(src, CardNews)
        except ValidationError as e:
            err = e.errors()[0]
            failed.append((src.stem, f"원고 형식 오류 — {'.'.join(map(str, err['loc']))}: {err['msg']} (총 {e.error_count()}건)"))
            continue
        except ValueError as e:
            failed.append((src.stem, f"원고 JSON 을 읽지 못했습니다: {e}"))
            continue
        if card.id != src.stem or card.week != week:
            failed.append((src.stem, f"파일 안의 id/week({card.id}, {card.week})가 파일 이름·주차({src.stem}, {week})와 다릅니다."))
            continue
        try:
            res = render_card(card, cfg)
        except CardRenderError as e:
            failed.append((card.id, str(e)))
            continue
        except OSError as e:  # 글꼴 없음·디스크 쓰기 실패 등
            failed.append((card.id, f"파일 오류: {e}"))
            continue
        record(week, res)
        done.append(res)
        lay = " ".join(f"{k}={v}" for k, v in res.layouts.items())
        log(f"[완료] {card.id}: {len(res.jpgs)}장 → {rel(res.jpgs[0].parent)}/ (레이아웃 {lay})")
        for w in res.warnings:
            log(f"[경고] {card.id}: {w}")
    return done, failed
