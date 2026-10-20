"""차례 기준 장·절 정비 — 차례 쪽에서 장·절 제목과 단계를 읽고, 본문의 같은 줄을 MD 제목('#'×단계)으로 바꾼다.

MyOCR(engine/toc_headings.py)와 01_OCR_스캔파일텍스트추출프로그램(engine/core/toc_headings.py)이
**바이트 그대로 같은 파일**을 쓴다(noise_rules·korean_corrector 와 같은 방식). 고칠 때는 두 곳을 함께 맞춘다.
외부 모듈에 기대지 않는다(표준 라이브러리만).

차례 행은 두 가지로 받는다:
  - MyOCR book_layout: OCR 상자 좌표로 같은 높이 상자를 묶은 행 (extract_toc_rows)
  - 01_OCR text_cleaner: 쪽 글의 줄 (extract_toc_text — 따로 읽힌 쪽번호 줄을 제목 줄에 붙인다)
출처 intent: MyOCR docs/intents/2026-10-20-책구조정비.md
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

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
CHAPTER_LABEL_RE = re.compile(r'^(?:제\s*\d+\s*[장부]|part\s*\d+|chapter\s*\d+)$', re.I)


def front_window(n: int) -> int:
    """차례를 찾는 앞쪽 범위(쪽 수)."""
    return min(n, _TOC_FRONT_MAX, max(15, int(n * 0.15)))


def strip_subtitles(texts: list[str]) -> list[str]:
    """한 행의 조각들 — 한글 제목 행에 붙은 영문 부제('Segment of One 91')는 쪽번호만 남긴다."""
    if not any(re.search(r'[가-힣]', t) for t in texts):
        return texts
    return [t if re.search(r'[가-힣]', t) or not re.search(r'[A-Za-z]', t)
            else (m.group(1) if (m := re.search(r'(\d{1,3})$', t)) else "") for t in texts]


_NUM_ONLY_RE = re.compile(r'^[\s·.…‥\-/]*(\d{1,3})\s*$')
_LABEL_ONLY_RE = re.compile(r'^\d{1,2}\s*[.)]$')
_PLACEHOLDER_RE = re.compile(r'^\[[^\]]*\]$|^[─━\-=\s]*비텍스트\s*영역')
_FULLWIDTH_PAREN = str.maketrans("（）", "()")     # 텍스트층 PDF 의 전각 괄호('（금요일 저녁）-53')


def rows_from_text(page: str) -> list[tuple[str, float]]:
    """쪽 글의 줄 → 차례 행. OCR 이 쪽번호를 따로 읽어 제목 다음 줄('1.경영은 기업경영이다' / '32')로 오면
    앞 줄에 붙인다(앞 줄에 이미 번호가 있으면 다음 줄 앞의 번호로 본다)."""
    lines = [l.strip().translate(_FULLWIDTH_PAREN) for l in page.split("\n") if l.strip()]
    out: list[str] = []
    pending = None
    label = ""
    for l in lines:
        if _LABEL_ONLY_RE.match(l):     # 번호표만 따로 읽힌 줄('5.' / '끝까지 생각한다…') → 다음 줄 앞에
            label = l + " "
            continue
        l, label = label + l, ""
        m = _NUM_ONLY_RE.match(l)
        if m and out and not _TOC_ROW_RE.match(out[-1]):
            out[-1] += " " + m.group(1)
            continue
        if m:
            pending = m.group(1)
            continue
        out.append(" ".join(strip_subtitles([l])) + (" " + pending if pending and not _TOC_ROW_RE.match(l) else ""))
        pending = None
    return [(t, 0.0) for t in out]


def extract_toc_text(pages: list[str]) -> tuple[list[dict], list[int]]:
    """쪽 글들에서 차례 — 01_OCR 정제 경로(줄 좌표 없음)."""
    return extract_toc_rows([rows_from_text(p) for p in pages[:front_window(len(pages))]])


_OUTER_MATTER_RE = re.compile(
    r'^(?:머리말|서문|프롤로그|prologue|들어가며|들어가는글|여는글|에필로그|epilogue|나가며|맺는글|맺음말|후기|'
    r'감사의?말|옮긴이의?말|역자의?말|역자후기|참고문헌|주석?|미주|부록|찾아보기|색인|index)$', re.I)


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


def extract_toc_rows(rows_by_page: list[list[tuple[str, float]]]) -> tuple[list[dict], list[int]]:
    """앞쪽 쪽들의 행(글, 왼쪽 x)에서 첫 차례 덩어리를 찾아 [{level, title, page}] 와 차례 쪽 번호들.
    못 찾으면 ([], [])."""
    win = len(rows_by_page)
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
            if _PLACEHOLDER_RE.match(t):   # 그림·표 자리 표시('[그림 1: 이미지 감지됨]'·'── 비텍스트 영역 ──')
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
_TRAILING_NO_RE = re.compile(r'[가-힣A-Za-z)]\s*\d{1,3}\s*$')
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
        ends_no = not re.search(r'\d\s*$', e["title"])

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
                # 쪽번호가 붙은 줄은 제목이 아니라 머리글이다 — 첫 등장 머리글은 정제가 일부러 남긴다
                # ('I . 자신의 길을 찾는다 17', 1분의사결정 텍스트층 PDF)
                if ends_no and _TRAILING_NO_RE.search(flat[s][2]):
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
                        if not in_list(s, t) and not toc_row(t):
                            return s, t - s + 1
                        break
                    t += 1
            return None

        def toc_row(t: int) -> bool:
            """바로 다음 줄이 쪽번호뿐 — 차례로 못 알아본 차례 쪽의 행('노동' / '193', 1001가지기독교명언)."""
            # 그 쪽이 차례 모양(쪽번호뿐인 줄이 둘 이상)일 때만 — 장 표제지 제목 다음 쪽 첫 줄이 쪽번호('15')인 것은 제목
            pi, q, nums = flat[t][0], page_start.get(flat[t][0], 0), 0
            while q < len(flat) and flat[q][0] == pi:
                nums += bool(_NUM_ONLY_RE.match(flat[q][2].strip()))
                q += 1
            if nums < 2:
                return False
            k = t + 1          # 쪽이 바뀌어도 본다 — 차례 행이 쪽 끝에서 끊긴다('행위에 근거한 구원?' / 다음 쪽 '968')
            while k < len(flat) and not flat[k][2].strip():
                k += 1
            return k < len(flat) and bool(_NUM_ONLY_RE.match(flat[k][2].strip()))

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




def report(entries: list[dict], stats: dict) -> str:
    """사람용 보고 한 줄."""
    if not entries:
        return "차례를 찾지 못해 장·절 정비 없이 정제"
    msg = f"차례 {len(entries)}항목 중 {stats.get('found', 0)}개를 본문 제목으로 정비"
    if stats.get("inferred"):
        msg += f"(+못 찾은 장 제목 {stats['inferred']}개는 아래 항목 앞에 넣음)"
    if stats.get("missing"):
        msg += f" · 본문에서 못 찾음: {', '.join(stats['missing'][:3])}" + (" 등" if len(stats["missing"]) > 3 else "")
    return msg
