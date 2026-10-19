"""책 구조 정비 — 여백(쪽번호·머리말·꼬리말) 제거 → 차례 기준 장·절 제목 정비 → MD/TXT 출력.

'일반 텍스트' 모드의 PDF 처리 순서 (docs/intents/2026-10-20-책구조정비.md):
  1. strip_margins   줄 위치(OCR 상자·PDF 좌표)로 쪽 위·아래 여백의 쪽번호·반복 머리말·꼬리말 제거
  2. page_texts      줄 사이 간격·들여쓰기로 단락 경계(빈 줄)를 넣어 쪽 글로
  3. extract_toc     앞부분 차례 쪽에서 장·절 제목과 단계를 읽는다
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
_SENT_END_RE = re.compile(r'[.!?…。”’"\'」』)]\s*$')
_PARA_GAP_RATIO = 0.6        # 위 줄과 '보통 줄 간격 + 줄 높이의 이 배수' 이상 떨어지면 새 단락(절 사이 빈 줄)
_INDENT_RATIO = 0.6          # 앞뒤 줄보다 줄 높이의 이만큼 이상 들여 쓰면 단락 첫 줄


def _overlap_x(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0])


def page_texts(pages: list[dict]) -> list[str]:
    """쪽마다 줄을 잇되, 간격이 크게 벌어지거나 들여 쓴 단락 첫 줄 앞에 빈 줄을 넣는다.
    들여쓰기는 앞 줄이 문장으로 끝났거나 짧게 끝났을 때만 믿는다(기울어진 스캔은 줄마다 x 가 흔들린다)."""
    out = []
    for p in pages:
        lines = p["lines"]
        hs = [_height(l) for l in lines if len(l[0]) >= 5]
        hm = median(hs) if hs else 0
        right = max((_box(l)[2] for l in lines), default=0)
        usual = max(0.0, _usual_gap(lines))
        buf: list[str] = []
        dropcap = ""
        for i, l in enumerate(lines):
            if dropcap:
                buf.append(dropcap + l[0])
                dropcap = ""
                continue
            if i and hm:
                prev, b = _box(lines[i - 1]), _box(l)
                nxt = _box(lines[i + 1]) if i + 1 < len(lines) else None
                same_col = _overlap_x(prev, b)
                gap = same_col and b[1] - prev[3] >= usual + hm * _PARA_GAP_RATIO
                base = min(prev[0], nxt[0]) if nxt and _overlap_x(nxt, b) else prev[0]
                ended = _SENT_END_RE.search(lines[i - 1][0]) or prev[2] < right - hm * 2
                indent = same_col and b[0] - base >= hm * _INDENT_RATIO and ended
                if gap or indent:
                    buf.append("")
            # 장식 첫 글자(드롭캡): 크게 따로 읽힌 '현' + 다음 줄 '실에 대한 …' → '현실에 대한 …' ('50'+'여 년 전')
            if hm and re.fullmatch(r'[가-힣A-Z]|\d{1,2}', l[0]) and _height(l) >= hm * 1.5 and i + 1 < len(lines):
                dropcap = l[0]
                continue
            buf.append(l[0])
        out.append("\n".join(buf))
    return out


# ── 3. 차례 ──────────────────────────────────────────────────────────────────
_TOC_TITLE_RE = re.compile(r'^(?:차\s*[례레]|목\s*차|contents?)$', re.I)
_TOC_ROW_RE = re.compile(r'^[·•\-*\s]*(.*?)[\s.·…‥:\-/~]*(\d{1,3})\s*$')
_TOC_MIN_ROWS = 5
_TOC_ROW_RATIO = 0.35
_TOC_FRONT_MAX = 40
# 단계 꼴 — 위 단계부터. 꼴이 없는 항목(머리말·'제1장에 들어가면서')은 바로 앞 장의 아래 단계
_LEVEL_PATTERNS = [
    re.compile(r'^(?:제\s*\d+\s*부|part\s*\d+|\d+\s*부(?![가-힣]))', re.I),
    # 장 — 로마 숫자 'Ⅱ.머리를 써라' 포함(OCR 이 'ⅡI.'·'N.'(Ⅳ)·'IN.' 으로 읽는다 — 1분의사결정 차례)
    re.compile(r'^(?:제\s*\d+\s*장(?!\s*(?:에|을|의|맺음|요약|정리|마치))|chapter\s*\d+|\d+\s*장(?![가-힣])|'
               r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ][IⅠ]?\s*[.,]|(?:[IVXM]{1,4}|N|IN|INV)\s*[.,]\s*(?=[가-힣]))', re.I),
    re.compile(r'^\d+\s*[.)](?!\d)'),
    # 1.1 꼴은 뒤에 띄어쓰기·끝이 와야 한다 — '3.21세기의 경쟁우위' 는 3 절이다(BCG의6가지성공전략)
    re.compile(r'^(?:\d+\.\d+(?=\s|$|[.)])|\(\d+\)|[가-하]\.)'),
]
_CHAPTER_LABEL_RE = re.compile(r'^(?:제\s*\d+\s*[장부]|part\s*\d+|chapter\s*\d+)$', re.I)
_DECOR_RATIO = 2.5           # 차례 쪽에서 줄 높이의 이만큼 넘는 큰 글자는 장식('BOSTON'·'GROUP' 로고 글자)


_OUTER_MATTER_RE = re.compile(
    r'^(?:머리말|서문|프롤로그|prologue|들어가며|들어가는글|여는글|에필로그|epilogue|나가며|맺는글|맺음말|후기|'
    r'감사의?말|옮긴이의?말|역자의?말|역자후기|참고문헌|주석?|미주|부록|찾아보기|색인|index)$', re.I)


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
        ls.sort(key=lambda l: (not _CHAPTER_LABEL_RE.match(l[0].strip()), _box(l)[0]))
        texts = [l[0].strip() for l in ls]
        if any(re.search(r'[가-힣]', t) for t in texts):
            texts = [t if re.search(r'[가-힣]', t) or not re.search(r'[A-Za-z]', t)
                     else (m.group(1) if (m := re.search(r'(\d{1,3})$', t)) else "") for t in texts]
        out.append((" ".join(t for t in texts if t), _box(ls[0])[0]))
    return out


def _numbered_rows(rows) -> int:
    return sum(1 for t, _ in rows if (m := _TOC_ROW_RE.match(t)) and len(re.findall(r'[가-힣A-Za-z]', m.group(1))) >= 2)


def _is_toc_rows(rows, ratio: float = _TOC_ROW_RATIO, min_rows: int = _TOC_MIN_ROWS) -> bool:
    if len(rows) < min_rows:
        return False
    k = _numbered_rows(rows)
    avg = sum(len(t) for t, _ in rows) / len(rows)
    return k >= min_rows and k / len(rows) >= ratio and avg <= 40


def _level_kind(title: str) -> int | None:
    t = title.strip()
    return next((i for i, rx in enumerate(_LEVEL_PATTERNS) if rx.match(t)), None) if t else None


def _clean_title(t: str) -> str:
    t = re.sub(r'[\s.·…‥]{3,}', ' ', t)          # 점선 이음줄
    return re.sub(r'\s+', ' ', t).strip(' -–—:/·.')


def extract_toc(pages: list[dict]) -> tuple[list[dict], list[int]]:
    """앞부분의 차례 쪽 덩어리에서 [{level, title, page}] 와 차례 쪽 번호들. 못 찾으면 ([], [])."""
    n = len(pages)
    win = min(n, _TOC_FRONT_MAX, max(15, int(n * 0.15)))
    rows_by_page = [_rows(p) for p in pages[:win]]
    start = next((i for i in range(win) if _is_toc_rows(rows_by_page[i])), None)
    if start is None:
        return [], []
    toc_pages = [start]
    j = start + 1
    # 이어지는 차례 쪽은 항목이 몇 줄뿐일 수 있다(1분의사결정 둘째 차례 쪽 6줄)
    while j < win and (not rows_by_page[j] or _is_toc_rows(rows_by_page[j], 0.25, 3)):
        if rows_by_page[j]:
            toc_pages.append(j)
        j += 1
    all_rows = [t for pi in toc_pages for t, _ in rows_by_page[pi]]
    hangul_book = sum(bool(re.search(r'[가-힣]', t)) for t in all_rows) >= len(all_rows) * 0.6
    raw: list[list] = []       # [제목, 쪽번호, 글머리표(·)로 시작했나]
    for pi in toc_pages:
        for t, _ in rows_by_page[pi]:
            t = t.strip()
            if not t or _TOC_TITLE_RE.match(t.replace(" ", "")):
                continue
            m = _TOC_ROW_RE.match(t)
            title, no = (m.group(1), int(m.group(2))) if m else (t, None)
            title = _clean_title(title)
            # 쪽번호만 남은 줄·영문 부제 줄('Value Management 45')은 항목이 아니다 — 번호는 앞 항목 것
            subtitle = hangul_book and not re.search(r'[가-힣]', title)
            if (subtitle or not re.search(r'[가-힣A-Za-z]', title)) and no is not None:
                if raw and raw[-1][1] is None:
                    raw[-1][1] = no
                continue
            # 차례 쪽 꼬리말('차례21')은 여백에서 못 지웠을 때 여기서 거른다
            if subtitle or len(re.findall(r'[가-힣A-Za-z]', title)) < 2 or _TOC_TITLE_RE.match(title.replace(" ", "")):
                continue
            raw.append([title, no, bool(re.match(r'[·•]', t))])
    # 두 줄로 넘어간 긴 제목: 쪽번호 없는 줄 + 단계 꼴 없는 다음 쪽번호 줄 → 한 항목. 앞 줄에 단계 꼴이 있으면
    # 다음 줄이 '(' 로 시작할 때만('4.여러 가지 … 안다' / '(금요일 저녁)·53') — 장 제목 뒤 '제1장에 들어가면서 27' 은 따로
    merged: list[list] = []
    for title, no, bullet in raw:
        if (merged and merged[-1][1] is None and no is not None and _level_kind(title) is None
                and (_level_kind(merged[-1][0]) is None or title.startswith("("))):
            merged[-1][0] += " " + title
            merged[-1][1] = no
        else:
            merged.append([title, no, bullet])
    kinds = sorted({k for t, _, _ in merged if (k := _level_kind(t)) is not None})
    level_of = {k: i + 1 for i, k in enumerate(kinds)}
    entries, container = [], 0
    for title, no, bullet in merged:
        k = _level_kind(title)
        if k is not None:
            lv = level_of[k]
            if k <= 1:                # 부·장 — 꼴 없는 항목이 이 아래에 든다
                container = lv
        elif bullet or _OUTER_MATTER_RE.match(title.replace(" ", "")):
            lv = 1                    # 앞뒤 부속('·역자의 말'·'·감사의 말'·'찾아보기')은 장 밖
            container = 0
        else:
            lv = container + 1 if container else 1
        entries.append({"level": lv, "title": title, "page": no})
    # 장 바로 앞의 쪽번호·단계 꼴 없는 짧은 표지('주주가치' → '제2장 가치경영')는 앞 장의 절이 아니라 장과 같은 단계
    for i in range(len(entries) - 1):
        e, nxt = entries[i], entries[i + 1]
        if (e["page"] is None and _level_kind(e["title"]) is None and _level_kind(nxt["title"]) in (0, 1)
                and nxt["level"] < e["level"]):
            e["level"] = nxt["level"]
    return entries, toc_pages


def _norm(s: str) -> str:
    return re.sub(r'[^0-9A-Za-z가-힣]', '', s).lower()


def _norm_hangul(s: str) -> str:
    """한글 제목 비교용 — 영문 부제('segment of one')는 뺀다."""
    return re.sub(r'[^0-9가-힣]', '', s)


# 제목 앞 번호표 — 본문 제목에는 없을 수 있다(BCG 장 표제지 '세그먼트 어브 원', 1분의사결정 '혼돈에서 탈출한다')
_LABEL_RE = re.compile(r'^\s*(?:제\s*\d+\s*[장부]|part\s*\d+|chapter\s*\d+|\d+\s*[.)]|'
                       r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ][IⅠ]?\s*[.,]|(?:[IVXM]{1,4}|N|IN|INV)\s*[.,])\s*', re.I)


_MATCH_MIN = 0.8
_PAGE_SLACK_BEFORE = 2       # 차례 쪽번호로 어림한 PDF 쪽보다 이만큼 앞부터
_PAGE_SLACK_AFTER = 10       # 이만큼 뒤까지 찾는다(스캔에 겹친 쪽·빠진 쪽·삽화 쪽이 끼면 뒤로 밀린다)
_PAGE_SLACK_FAR = 8          # 근처에 없을 때 다시 찾는 앞쪽 한계
_MAX_JOIN = 3                # 본문에서 제목이 이만큼 줄로 나뉘어 있어도 찾는다('저자의 말' / '내일의 핵심문제')


def mark_headings(pages: list[str], entries: list[dict], toc_pages: list[int], stats: dict) -> list[str]:
    """차례 쪽을 비우고, 본문에서 차례 제목과 같은 줄(연이은 짧은 줄 묶음 포함)을 차례 순서대로 찾아
    '#'×단계 제목으로 바꾼다. 못 찾은 장(부) 제목은 그 아래 항목을 찾았으면 그 앞에 넣는다.
    stats 에 found·inferred·missing 을 채운다."""
    pages = ["" if i in set(toc_pages) else p for i, p in enumerate(pages)]
    flat = [(pi, li, l) for pi, p in enumerate(pages) for li, l in enumerate(p.split("\n"))]
    norms_all = [_norm(l) for _, _, l in flat]
    norms_han = [_norm_hangul(l) for _, _, l in flat]
    page_start = {}
    for idx, (pi, _, _) in enumerate(flat):
        page_start.setdefault(pi, idx)
    pos, hits, offset = 0, {}, None    # hits: 차례 항목 번호 → (flat 시작, 줄 수). offset: PDF 쪽 - 책 쪽
    for ei, e in enumerate(entries):
        han = bool(re.search(r'[가-힣]', e["title"]))
        norm, norms = (_norm_hangul, norms_han) if han else (_norm, norms_all)
        targets = [t for t in dict.fromkeys([norm(e["title"]), norm(_LABEL_RE.sub("", e["title"]))]) if len(t) >= 2]
        targets = targets[:1] + [t for t in targets[1:] if len(t) >= 4]
        if not targets:
            continue
        sm = SequenceMatcher(None, autojunk=False)

        def find(lo: int, hi: int):
            for target in targets:
                sm.set_seq2(target)
                got = find_one(lo, hi, target)
                if got:
                    return got
            return None

        def find_one(lo: int, hi: int, target: str):
            """(시작, 줄 수) — 제목이 여러 줄이면 사이의 빈 줄 하나까지 건너 잇는다('혼돈에서 탈출한다' / '' /
            '(금요일 아침)' — 큰 제목 사이 간격이 단락 빈 줄이 된다)."""
            for s in range(lo, hi):
                if not norms[s]:
                    continue
                cand, t, used = "", s, 0
                while t < len(flat) and used < _MAX_JOIN and flat[t][0] == flat[s][0]:
                    if not norms[t]:        # 빈 줄 하나만 건넌다
                        if norms[t - 1] and t + 1 < len(flat) and norms[t + 1]:
                            t += 1
                            continue
                        break
                    cand += norms[t]
                    used += 1
                    if len(cand) > len(target) * 1.4 + 3:
                        break
                    sm.set_seq1(cand)
                    if sm.real_quick_ratio() >= _MATCH_MIN and sm.quick_ratio() >= _MATCH_MIN \
                            and sm.ratio() >= _MATCH_MIN:
                        if not in_list(s, t):
                            return s, t - s + 1
                        break
                    t += 1
            return None

        def in_list(s: int, t: int) -> bool:
            """장 표제지의 절 목록('1. 주주가치란 무엇인가' / '2. TBR 산출법' …) 안의 줄인가 — 바로 앞뒤 줄이
            이웃 차례 항목으로 시작한다(BCG의6가지성공전략 장 첫 쪽)."""
            def near(idx: int, step: int) -> str:     # 영문까지 비교('2.TBR 산출법')
                idx += step
                while 0 <= idx < len(flat) and flat[idx][0] == flat[s][0] and not norms_all[idx]:
                    idx += step
                return norms_all[idx] if 0 <= idx < len(flat) and flat[idx][0] == flat[s][0] else ""
            before, after = near(s, -1), near(t, 1)
            for j in list(range(max(0, ei - 3), ei)) + list(range(ei + 1, min(len(entries), ei + 4))):
                if entries[j]["level"] != e["level"]:   # 장 제목 바로 뒤 첫 절은 목록이 아니다
                    continue
                other = _norm(_LABEL_RE.sub("", entries[j]["title"]))
                line = before if j < ei else after
                if len(other) >= 4 and line and SequenceMatcher(
                        None, line[-len(other) - 2:] if j < ei else line[:len(other) + 2], other).ratio() >= _MATCH_MIN:
                    return True
            return False

        def at(q: int) -> int:     # q 쪽(없으면 그 뒤 첫 쪽)의 flat 시작 번호
            return page_start.get(next((r for r in range(max(0, q), len(pages)) if r in page_start),
                                       len(pages)), len(flat))

        # 앞서 찾은 항목으로 PDF 쪽과 책 쪽 차이를 알면 그 근처에서만 찾는다 — 장 첫머리에 절 제목과 같은
        # 문장이 목록으로 먼저 나온다(21세기지식경영 '경영의 범위는 법적으로 규정된다'). 근처에 없으면 조금 더
        # 앞까지 — 스캔에서 빠진 쪽이 있으면 차이가 줄어든다(1분의사결정 1장 15쪽이 PDF 11쪽)
        # 쪽번호 없는 항목(장 제목·표지)은 다음 쪽번호 항목보다 뒤에 있을 수 없다 — 짧은 표지 '주주가치' 가
        # 본문 아무 데나 붙지 않게(BCG의6가지성공전략)
        later = next((x["page"] for x in entries[ei + 1:] if x["page"] is not None), None)
        if offset is not None and e["page"] is not None:
            exp = e["page"] + offset
            near = max(pos, at(exp - _PAGE_SLACK_BEFORE))
            found = find(near, at(exp + _PAGE_SLACK_AFTER + 1)) or find(max(pos, at(exp - _PAGE_SLACK_FAR)), near)
        elif offset is not None and later is not None:
            found = find(pos, at(later + offset + _PAGE_SLACK_AFTER + 1))
        else:
            found = find(pos, len(flat))
        if found:
            hits[ei] = found
            pos = found[0] + found[1]
            if e["page"] is not None:
                offset = flat[found[0]][0] - e["page"]
    # 장 표제지는 세로쓰기·장식 글꼴이라 OCR 이 못 읽는 일이 많다(21세기지식경영 '제 / 새로운 / 으교') —
    # 그 장의 아래 단계 항목 가운데 본문에서 처음 찾은 것 앞에 장 제목을 넣는다
    inferred = {}
    for ei, e in enumerate(entries):
        if ei in hits:
            continue
        for j in range(ei + 1, len(entries)):
            if entries[j]["level"] <= e["level"]:
                break
            if j in hits:
                inferred[ei] = hits[j][0]
                break
    lines = [l for _, _, l in flat]
    starts = {s for s, _ in hits.values()}
    insert_before: dict[int, list[str]] = {}
    for ei, s in inferred.items():
        insert_before.setdefault(s, []).append(_heading(entries[ei]))
    for ei, (s, k) in hits.items():
        lines[s] = _heading(entries[ei])
        for t in range(s + 1, s + k):
            lines[t] = None
    by_page: list[list[str]] = [[] for _ in pages]
    for idx, (pi, _, _) in enumerate(flat):
        for h in insert_before.get(idx, []):
            by_page[pi] += ["", h, ""]
        l = lines[idx]
        if l is None:
            continue
        by_page[pi] += ["", l, ""] if idx in starts else [l]
    stats.update(found=len(hits), inferred=len(inferred),
                 missing=[e["title"] for ei, e in enumerate(entries) if ei not in hits and ei not in inferred])
    return ["\n".join(p) for p in by_page]


def _heading(e: dict) -> str:
    return "#" * min(e["level"], 6) + " " + e["title"]


_HEADING_RE = re.compile(r'^#{1,6} \S')


def space_headings(text: str) -> str:
    """MD 제목 줄 앞뒤에 빈 줄을 한 줄씩."""
    out: list[str] = []
    for l in text.split("\n"):
        if _HEADING_RE.match(l):
            if out and out[-1].strip():
                out.append("")
            out += [l, ""]
        elif l.strip() or (out and out[-1].strip()):
            out.append(l)
    return re.sub(r'\n{3,}', '\n\n', "\n".join(out)).strip()


# ── 4. 출력 ──────────────────────────────────────────────────────────────────
def to_markdown(body: str, entries: list[dict]) -> str:
    if not entries:
        return body
    toc = "\n".join("  " * (e["level"] - 1) + "- " + e["title"] for e in entries)
    return f"# 차례\n\n{toc}\n\n{body}"


def to_text(body: str, entries: list[dict]) -> str:
    """MD 와 같은 내용·줄바꿈에서 '#'·목록 기호만 뺀다."""
    plain = "\n".join(re.sub(r'^#{1,6} ', '', l) if _HEADING_RE.match(l) else l for l in body.split("\n"))
    if not entries:
        return plain
    toc = "\n".join("  " * (e["level"] - 1) + e["title"] for e in entries)
    return f"차례\n\n{toc}\n\n{plain}"


def pdf_to_text(engine, path: str, drop_matter: bool = False, progress_cb=None) -> tuple[str, str, list[str]]:
    """PDF 한 권 → (MD, TXT, 보고 줄들). engine 은 ocr_engine.OCREngine — GUI 와 일괄 처리가 함께 쓴다."""
    return refine_pdf_pages(engine.ocr_pdf(path, progress_cb=progress_cb, with_boxes=True), drop_matter)


def refine_pdf_pages(pages: list[dict], drop_matter: bool = False) -> tuple[str, str, list[str]]:
    """ocr_pdf(with_boxes=True) 쪽들 → (MD, TXT, 보고 줄들). 처리 순서는 모듈 설명 참고."""
    pages, n_margin = strip_margins(pages)
    entries, toc_pages = extract_toc(pages)
    texts = page_texts(pages)
    stats: dict = {}
    structure = (lambda ps: mark_headings(ps, entries, toc_pages, stats)) if entries else None
    body, notes = text_refiner.refine_pages(texts, drop_matter=drop_matter, structure=structure)
    body = space_headings(body)
    head = []
    if n_margin:
        head.append(f"여백의 쪽번호·머리말·꼬리말 {n_margin}줄 제거")
    if entries:
        msg = f"차례 {len(entries)}항목 중 {stats.get('found', 0)}개를 본문 제목으로 정비"
        if stats.get("inferred"):
            msg += f"(+못 찾은 장 제목 {stats['inferred']}개는 아래 항목 앞에 넣음)"
        if stats.get("missing"):
            msg += f" · 본문에서 못 찾음: {', '.join(stats['missing'][:3])}" + (" 등" if len(stats["missing"]) > 3 else "")
        head.append(msg)
    else:
        head.append("차례를 찾지 못해 장·절 정비 없이 정제")
    return to_markdown(body, entries), to_text(body, entries), head + notes
