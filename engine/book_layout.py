"""책 구조 정비 — 여백(쪽번호·머리말·꼬리말) 제거 → 차례 기준 장·절 제목 정비 → MD/TXT 출력.

'일반 텍스트' 모드의 PDF 처리 순서 (docs/intents/2026-10-20-책구조정비.md):
  1. strip_margins   줄 위치(OCR 상자·PDF 좌표)로 쪽 위·아래 여백의 쪽번호·반복 머리말·꼬리말 제거
  2. page_texts      줄 사이 간격·들여쓰기로 단락 경계(빈 줄)를 넣어 쪽 글로
  3. extract_toc     앞부분 차례 쪽에서 장·절 제목과 단계를 읽는다 (toc_headings — 01_OCR 과 같은 파일)
     mark_headings   본문에서 차례 제목과 같은 줄을 MD 제목('#'×단계)으로, 앞뒤 빈 줄
  4. text_refiner.refine_pages (기존 정제 — 제목 줄은 줄 잇기에서 보호)
  5. to_markdown / to_text   맨 앞 차례 목록 + 본문 (TXT 는 '#'·목록 기호만 뺀다)

차례를 찾지 못하면 3단계만 건너뛴다(여백 제거·단락 경계·정제는 그대로, 보고에 알림).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from statistics import median

import text_refiner
import toc_headings

# ── 1. 여백 ──────────────────────────────────────────────────────────────────
# 실측(21세기지식경영 스캔 2334px 높이): 꼬리말 '제1장새로운경영패러다임39'·'4021세기지식경영'·'역자의말3'·'4'
# 는 쪽 아래 8~12% 띠에, 본문 끝 줄과 줄 높이 2배쯤 떨어져 있다. 위치만 보면 장 첫 쪽 위의 절 제목
# ('1.경영은 기업경영이다')도 같은 꼴이라, 위치 + 글자 모양(쪽번호뿐 / 여러 쪽에 반복)이 함께 맞을 때만 지운다.
_ZONE = 0.18                 # 쪽 위·아래 이 비율 안을 여백 띠로 본다 (1분의사결정 머리글은 위 15% 경계)
_SHORT_RATIO = 0.6           # 본문 꽉 찬 줄 너비의 이 비율보다 좁아야 머리말·꼬리말 후보
_GAP_RATIO = 0.5             # 이웃 줄과 '보통 줄 간격 + 줄 높이의 이 배수' 이상 떨어져야 후보
_REPEAT_MIN_PAGES = 3        # 같은 글자(숫자 뺀)가 이만큼 쪽의 여백에 있으면 머리말·꼬리말
_REPEAT_MIN_NUMBERED = 2     # 쪽번호가 붙은 꼴('저자의말19')은 짧은 절(3~4쪽, 홀수 쪽에만 꼬리말)도 잡게 2쪽
_KEY_SIMILAR = 0.75          # OCR 이 쪽마다 달리 읽은 글자('저자의말'·'저자의만')를 같은 묶음으로
_EDGE_NO_RE = re.compile(r'^\d{1,4}\s*\S|\S\s*\d{1,4}$')
_BIG_FONT_RATIO = 1.3        # 본문보다 이만큼 큰 글자는 제목일 수 있어 반복만으로는 지우지 않는다
_PAGE_NO_ONLY_RE = re.compile(r'^[\s\-–—·.()\[\]]*\d{1,4}[\s\-–—·.()\[\]]*$')


def _box(line):
    return line[2]


def _height(line) -> float:
    b = _box(line)
    return b[3] - b[1]


def _width(line) -> float:
    b = _box(line)
    return b[2] - b[0]


def _margin_key(text: str) -> str:
    return re.sub(r'[\d\s\W_]', '', text)


def _usual_gap(lines) -> float:
    """쪽의 보통 줄 사이 간격(같은 단의 위아래 줄). 상자가 겹치면 음수."""
    ys = sorted((_box(l) for l in lines), key=lambda b: b[1])
    gaps = [b[1] - a[3] for a, b in zip(ys, ys[1:]) if min(a[2], b[2]) > max(a[0], b[0])]
    return median(gaps) if gaps else 0.0


def _full_width(pages) -> float:
    """책에서 흔한 '꽉 찬 본문 줄' 너비 — 쪽마다 넓은 줄(상위 10%) 너비의 중앙값."""
    per_page = []
    for p in pages:
        ws = sorted(_width(l) for l in p["lines"] if len(l[0]) >= 15)
        if ws:
            per_page.append(ws[int(len(ws) * 0.9)])
    return median(per_page) if per_page else 0.0


def _margin_candidates(page, full_w: float) -> list[tuple[int, bool]]:
    """쪽 위·아래 여백 띠에 홀로 떨어져 있는 줄 — [(줄 번호, 넓은 줄인가)]."""
    lines = page["lines"]
    hs = [_height(l) for l in lines if len(l[0]) >= 5]
    if not lines or not hs:
        return []
    hm, h = median(hs), page["h"]
    # 쪽의 보통 줄 사이 간격 — OCR 상자가 글자보다 커서 이웃 줄 상자가 겹치는 쪽(간격이 음수)도 있다
    # (21세기지식경영 192쪽: 줄 간격 -23px, 꼬리말 간격 64px, 줄 높이 78px)
    usual = max(0.0, _usual_gap(lines))
    out = []
    for i, l in enumerate(lines):
        x0, y0, x1, y1 = _box(l)
        cy = (y0 + y1) / 2
        top, bottom = cy < h * _ZONE, cy > h * (1 - _ZONE)
        if not (top or bottom):
            continue
        # 안쪽(본문 쪽) 줄과의 간격. 같은 높이의 다른 상자(따로 읽힌 쪽번호)는 빼고, 겹치면 음수
        # (DBR 2단 기사 맨 위 '418)' 은 바로 아래 줄과 겹쳐 있는 본문 조각이다)
        if top:
            inner = [b[1] - y1 for j, m in enumerate(lines) if j != i and ((b := _box(m))[1] + b[3]) / 2 > cy + hm * 0.3]
        else:
            inner = [y0 - b[3] for j, m in enumerate(lines) if j != i and ((b := _box(m))[1] + b[3]) / 2 < cy - hm * 0.3]
        if not inner or min(inner) >= usual + hm * _GAP_RATIO:
            out.append((i, bool(full_w) and _width(l) >= full_w * _SHORT_RATIO))
    # 같은 높이에 후보가 아닌 줄이 있으면 여백이 아니다 — 차례 쪽 맨 위 항목의 쪽번호('제1장맺음말 … 88')
    cand = {i for i, _ in out}
    return [(i, w) for i, w in out if not any(j not in cand and _same_row(lines, i, j)
                                              for j in range(len(lines)) if j != i)]


def _same_row(lines, i, j) -> bool:
    b, c = _box(lines[i]), _box(lines[j])
    return c[1] <= (b[1] + b[3]) / 2 <= c[3]


def strip_margins(pages: list[dict]) -> tuple[list[dict], int]:
    """쪽 위·아래 여백의 쪽번호·반복 머리말·꼬리말 줄을 지운다. (쪽들, 지운 줄 수).
    짧은 줄: 쪽번호뿐이거나, 숫자를 뺀 글자가 여러 쪽 여백에 반복될 때.
    넓은 줄: 글자가 똑같이 여러 쪽 여백에 반복될 때만(DBR 'Downloaded by … 2016-05-25' 워터마크)."""
    full_w = _full_width(pages)
    cands = [_margin_candidates(p, full_w) for p in pages]
    # 숫자를 뺀 글자 묶음별로 몇 쪽에 나오는지 — OCR 이 쪽마다 조금씩 다르게 읽어 비슷한 것끼리 묶는다
    groups: list[tuple[str, set[int]]] = []
    group_of = lambda k: next((g for g in groups if g[0] == k
                               or SequenceMatcher(None, g[0], k).ratio() >= _KEY_SIMILAR), None)
    wide_pages: dict[str, set[int]] = {}
    for n, (p, idx) in enumerate(zip(pages, cands)):
        for i, wide in idx:
            k = _margin_key(p["lines"][i][0])
            if wide:
                wide_pages.setdefault(k, set()).add(n)
                continue
            if len(k) < 2:
                continue
            g = group_of(k)
            if g is None:
                groups.append((k, {n}))
            else:
                g[1].add(n)
    out, removed = [], 0
    for n, (p, idx) in enumerate(zip(pages, cands)):
        ls = p["lines"]
        hs = [_height(l) for l in ls if len(l[0]) >= 5]
        hm = median(hs) if hs else 0
        drop = set()
        for i, wide in idx:
            text = ls[i][0].strip()
            k = _margin_key(text)
            if wide:
                if len(k) >= 6 and len(wide_pages.get(k, ())) >= _REPEAT_MIN_PAGES:
                    drop.add(i)
                continue
            if _PAGE_NO_ONLY_RE.match(text):
                drop.add(i)
                continue
            g = group_of(k) if len(k) >= 2 else None
            numbered = bool(_EDGE_NO_RE.search(text))
            need = _REPEAT_MIN_NUMBERED if numbered else _REPEAT_MIN_PAGES
            # 큰 글자는 제목일 수 있어 반복만으로는 지우지 않는다 — 쪽번호가 붙은 꼴('DBR 2012 No.105 16')은 꼬리말
            if g and len(g[1]) >= need and (numbered or _height(ls[i]) <= hm * _BIG_FONT_RATIO):
                drop.add(i)
        # 쪽번호만인 줄도 같은 높이에 남는 글줄이 있으면 그 줄의 번호다(차례 '제1장맺음말 … 88')
        drop = {i for i in drop if not any(j not in drop and _same_row(ls, i, j) for j in range(len(ls)) if j != i)}
        removed += len(drop)
        out.append({**p, "lines": [l for i, l in enumerate(ls) if i not in drop]})
    return out, removed


# ── 2. 단락 경계 ──────────────────────────────────────────────────────────────
_PARA_GAP_RATIO = 0.6        # 위 줄과 '보통 줄 간격 + 줄 높이의 이 배수' 이상 떨어지면 새 단락(절 사이 빈 줄)
# 들여쓰기 문단 판정 — 01 rapidocr_engine 과 같은 기준(2026-10-18 실측: 문턱 0.6h 는 문단 시작 76곳 중 35곳만 잡음)
INDENT_MIN = 0.4             # 문단 첫 줄 들여쓰기 최소(줄 높이 배수)
INDENT_MAX = 3.5
MARGIN_TOL = 0.3             # 왼쪽 여백에 붙은 줄로 보는 흔들림(줄 높이 배수)
_SENT_END_LINE = re.compile(r'[.?!。！？]["”’\'」』)]*$')


def _overlap_x(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0])


def _geometry(lines, hm: float) -> dict:
    """쪽의 왼쪽 여백·오른쪽 끝 — 넓은 줄(가장 넓은 줄의 80%↑) 가운데 가장 왼쪽에 몰린 줄들로 여백을 잡는다.
    들여 쓴 인용 단락 줄이 섞이면 중앙값이 오른쪽으로 밀려 본문 줄이 '여백 아님'이 됐다(01 2천년의강의 실측)."""
    x0 = [_box(l)[0] for l in lines]
    x1 = [_box(l)[2] for l in lines]
    full = max((b - a for a, b in zip(x0, x1)), default=0) * 0.8
    wide = [i for i in range(len(lines)) if x1[i] - x0[i] >= full] or list(range(len(lines)))
    left = min(x0[i] for i in wide)
    margin = median([x0[i] for i in wide if x0[i] <= left + 0.5 * hm])
    return {"margin": margin, "right": median([x1[i] for i in wide]),
            "at_margin": [abs(x - margin) < MARGIN_TOL * hm for x in x0]}


def page_texts(pages: list[dict]) -> list[str]:
    """쪽마다 줄을 잇되, 간격이 크게 벌어지거나 들여 쓴 단락 첫 줄 앞에 빈 줄을 넣는다(01 과 같은 들여쓰기 기준).
    들여쓰기 문단이 있는 쪽에서는 꽉 찬 줄이 마침표로 끝나고 다음 줄이 들여쓰기 없이 이어지면 같은 단락으로
    한 줄에 잇는다 — 글자만 보는 줄 잇기는 줄 끝 마침표에서 늘 단락을 끊었다(01 표본 37쪽 18곳)."""
    out = []
    for p in pages:
        lines = p["lines"]
        hs = [_height(l) for l in lines if len(l[0]) >= 5]
        hm = median(hs) if hs else 0
        usual = max(0.0, _usual_gap(lines))
        g = _geometry(lines, hm) if lines and hm else None
        starts = set()
        if g:
            for i in range(1, len(lines)):
                prev, b = _box(lines[i - 1]), _box(lines[i])
                prev_ok = g["at_margin"][i - 1] or prev[2] < g["right"] - 2 * hm
                if (INDENT_MIN * hm <= b[0] - g["margin"] <= INDENT_MAX * hm and b[2] >= g["right"] - hm
                        and prev_ok and _overlap_x(prev, b)):
                    starts.add(i)
        buf: list[str] = []
        dropcap = ""
        for i, l in enumerate(lines):
            if dropcap:
                buf.append(dropcap + l[0])
                dropcap = ""
                continue
            if i and hm:
                prev, b = _box(lines[i - 1]), _box(l)
                same_col = _overlap_x(prev, b)
                gap_px = b[1] - prev[3]
                if (same_col and gap_px >= usual + hm * _PARA_GAP_RATIO) or i in starts:
                    buf.append("")
                elif (starts and g and buf and buf[-1] and prev[2] >= g["right"] - hm
                      and (g["at_margin"][i - 1] or i - 1 in starts) and _SENT_END_LINE.search(lines[i - 1][0].rstrip())
                      and g["at_margin"][i] and gap_px <= max(1.5 * usual, usual + 0.3 * hm)):
                    buf[-1] = buf[-1].rstrip() + " " + l[0].strip()   # 줄 끝 마침표 — 같은 단락
                    continue
            # 장식 첫 글자(드롭캡): 크게 따로 읽힌 '현' + 다음 줄 '실에 대한 …' → '현실에 대한 …' ('50'+'여 년 전')
            if hm and re.fullmatch(r'[가-힣A-Z]|\d{1,2}', l[0]) and _height(l) >= hm * 1.5 and i + 1 < len(lines):
                dropcap = l[0]
                continue
            buf.append(l[0])
        out.append("\n".join(buf))
    return out


# ── 3. 차례 (공통 모듈 toc_headings 를 쓴다) ───────────────────────────────────
_DECOR_RATIO = 2.5           # 차례 쪽에서 줄 높이의 이만큼 넘는 큰 글자는 장식('BOSTON'·'GROUP' 로고 글자)


def _rows(page) -> list[tuple[str, float]]:
    """같은 높이의 상자(차례의 제목·쪽번호가 따로 읽힌 것)를 한 줄로 — (글, 왼쪽 x).
    디자인된 차례(BCG의6가지성공전략): 큰 장식 글자는 빼고, 한글 제목 줄에 붙은 영문 부제
    ('Segment of One 91')는 쪽번호만 남기고, 오른쪽에 따로 놓인 '제3장' 은 제목 앞으로 옮긴다."""
    hs = [_height(l) for l in page["lines"] if len(l[0].strip()) >= 2]
    hm = median(hs) if hs else 0
    rows: list[list] = []
    for l in sorted(page["lines"], key=lambda l: (_box(l)[1] + _box(l)[3]) / 2):
        if hm and _height(l) > hm * _DECOR_RATIO:
            continue
        b = _box(l)
        cy = (b[1] + b[3]) / 2
        if rows and rows[-1][0][1] <= cy <= rows[-1][0][3]:
            rows[-1][1].append(l)
        else:
            rows.append([b, [l]])
    out = []
    for _, ls in rows:
        ls.sort(key=lambda l: (not toc_headings.CHAPTER_LABEL_RE.match(l[0].strip()), _box(l)[0]))
        texts = toc_headings.strip_subtitles([l[0].strip() for l in ls])
        out.append((" ".join(t for t in texts if t), _box(ls[0])[0]))
    return out


def extract_toc(pages: list[dict]) -> tuple[list[dict], list[int]]:
    """줄 좌표가 있는 쪽들에서 차례 — [{level, title, page}] 와 차례 쪽 번호들."""
    return toc_headings.extract_toc_rows([_rows(p) for p in pages[:toc_headings.front_window(len(pages))]])


def pdf_to_text(engine, path: str, drop_matter: bool = False, progress_cb=None) -> tuple[str, str, list[str]]:
    """PDF 한 권 → (MD, TXT, 보고 줄들). engine 은 ocr_engine.OCREngine — GUI 와 일괄 처리가 함께 쓴다."""
    return refine_pdf_pages(engine.ocr_pdf(path, progress_cb=progress_cb, with_boxes=True), drop_matter)


def refine_pdf_pages(pages: list[dict], drop_matter: bool = False) -> tuple[str, str, list[str]]:
    """ocr_pdf(with_boxes=True) 쪽들 → (MD, TXT, 보고 줄들). 처리 순서는 모듈 설명 참고."""
    pages, n_margin = strip_margins(pages)
    entries, toc_pages = extract_toc(pages)
    texts = page_texts(pages)
    stats: dict = {}
    structure = (lambda ps: toc_headings.mark_headings(ps, entries, toc_pages, stats)) if entries else None
    body, notes = text_refiner.refine_pages(texts, drop_matter=drop_matter, structure=structure)
    body = toc_headings.space_headings(body)
    head = [f"여백의 쪽번호·머리말·꼬리말 {n_margin}줄 제거"] if n_margin else []
    head.append(toc_headings.report(entries, stats))
    return toc_headings.to_markdown(body, entries), toc_headings.to_text(body, entries), head + notes
