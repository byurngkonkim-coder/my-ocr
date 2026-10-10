"""문장 정제·부속물 삭제 (규칙 기반, 오프라인) — '일반 텍스트' 모드 결과용.

01_OCR_스캔파일텍스트추출프로그램 의 core/text_cleaner.py · core/postprocessor.py 에서
LLM(Gemini) 없이 동작하는 결정론 단계만 옮겨 왔다. 함수 본문은 원본과 같게 유지한다
(원본 실측·회귀 테스트로 다듬어진 규칙이므로).

- 문장 정제: 쪽번호·반복 머리글/꼬리말 제거, 쪽 안의 끊긴 줄 잇기, 쪽 경계에서 끊긴 문장 잇기
- 부속물 삭제: 선두의 차례, 선두·말미의 판권(간기)·도서 광고 제거
  (표제지·헌사·약력처럼 형태가 뚜렷하지 않은 앞부속은 원본에서도 LLM이 처리 — 여기선 남는다)
"""
from __future__ import annotations

import re

import korean_corrector

# 문장이 '깔끔히' 끝났는지 판단
_SENT_END = re.compile(r'[.!?…。！？”’"\'\)\]》」』]\s*$')


def refine_pages(pages: list[str], drop_matter: bool = False) -> tuple[str, list[str]]:
    """쪽별 텍스트 → (정제된 전체 본문, 사람용 보고 줄들)."""
    # 빈 쪽도 남겨 두어 보고의 쪽 번호가 PDF 쪽과 맞게
    pages = [remove_noise_lines(normalize_chars((p or "").strip())) for p in pages]
    notes: list[str] = []
    if drop_matter:
        pages, notes = strip_book_matter(pages)
    pages = strip_page_numbers(pages)
    pages = strip_running_headers(pages)
    pages = [join_broken_lines(fix_hyphenated_words(p)) for p in pages]
    text, _ = rejoin_split_sentences("\n\n".join(p for p in pages if p.strip()))
    # 줄을 이은 뒤 문서 전체를 근거로 쪼개진 어절 결합 ('사 실은'→'사실은') — 파일형식변환기_MD 처럼
    # 교정을 줄 잇기 뒤에 돌린다. OCR 줄 단위 교정은 줄 사이에서 끊긴 어절을 볼 수 없다.
    text = korean_corrector.fix_split_tokens(text, document_corpus=text)
    text = fix_punctuation_spacing(text)
    return re.sub(r'\n{3,}', '\n\n', text).strip(), notes


# ── 문자 정규화·잡음 (파일형식변환기_MD core/text_postprocessor.py 에서 안전한 것만, 범위를 좁혀 옮김) ──
# 원본의 NFKC 는 'ㅋ' 같은 호환 자모를 조합용 자모로 바꿔 깨져 보이게 하므로 전각→반각만 한다.
# 원본의 기호 치환표('m2'→'m²', 'oC'→'℃')는 'item2'·'videoClip' 같은 단어 안까지 바꿔 옮기지 않았다.
_FULLWIDTH = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}
_FULLWIDTH[0x3000] = 0x20   # 전각 공백
_INVISIBLE_RE = re.compile(r"[﻿​‌‍⁠\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st"}


def normalize_chars(text: str) -> str:
    """전각 영숫자·구두점('，（Ａ１）')→반각, 비가시·제어문자 제거, NBSP→공백, 합자 분해,
    숫자 사이 I/l/O→1/1/0, 소문자 사이 I→l ('shouId'→'should'), 가운뎃점 잡음(ᆞ) 제거."""
    text = text.translate(_FULLWIDTH).replace("\xa0", " ")
    text = _INVISIBLE_RE.sub("", text)
    for lig, rep in _LIGATURES.items():
        text = text.replace(lig, rep)
    # 원본은 S·B·G 도 숫자로 바꾸지만 '12B34' 같은 제품·문서 번호를 망가뜨려 I·l·O 만 옮긴다
    text = re.sub(r"(?<=[0-9])[Il](?=[0-9])", "1", text)
    text = re.sub(r"(?<=[0-9])O(?=[0-9])", "0", text)
    # 원본은 숫자 1 도 l 로 바꾸지만 'v1beta' 같은 식별자를 망가뜨려 대문자 I 만
    text = re.sub(r"(?<=[a-z])I(?=[a-z])", "l", text)
    return re.sub(r"[ \t]*ᆞ+[ \t]*", " ", text)


def remove_noise_lines(text: str) -> str:
    """의미 있는 문자(한글·영문·숫자)가 2자 미만인 순수 잡음 줄 제거 ('l', '柴', '•■')."""
    return "\n".join(l for l in text.splitlines()
                     if not l.strip() or len(re.sub(r"[^\w가-힣]", "", l)) >= 2)


def fix_hyphenated_words(text: str) -> str:
    """줄 끝 하이픈으로 쪼개진 영단어 복원 ('infor-\\nmation'→'information').
    원본의 한글 규칙('효율적-\\n으로')은 옮기지 않았다 — 한국어는 하이픈으로 어절을 끊지 않아
    줄 끝 하이픈은 대개 진짜 하이픈('서울-\\n부산')이다."""
    return re.sub(r"([a-zA-Z])-\n\s*([a-z])", r"\1\2", text)


def fix_punctuation_spacing(text: str) -> str:
    """구두점 앞 공백 제거, 한글 앞 구두점 뒤 띄움, 괄호 안쪽 공백 제거.
    원본은 구두점 뒤 영문까지 띄워 'www.naver.com'·'e.g.' 를 망가뜨려 한글 앞만 띄운다."""
    text = re.sub(r"(?<=\S)[ \t]+([.,!?])(?=\s|$|[가-힣])", r"\1", text, flags=re.M)
    text = re.sub(r"([.,!?])([가-힣])", r"\1 \2", text)
    text = re.sub(r"([(\[])[ \t]+", r"\1", text)
    return re.sub(r"[ \t]+([)\]])", r"\1", text)


# ── 쪽번호 (원본은 LLM이 지우던 것 — 쪽 맨 첫/끝 줄의 숫자 단독 줄만 뗀다) ──────────
_PAGE_NO_LINE_RE = re.compile(r'^[-–—·\s]*\d{1,4}[-–—·\s]*$')


def strip_page_numbers(pages: list[str]) -> list[str]:
    out = []
    for p in pages:
        lines = p.splitlines()
        drop = {i for i in _edge_indices(lines) if _PAGE_NO_LINE_RE.match(lines[i].strip())}
        out.append("\n".join(l for i, l in enumerate(lines) if i not in drop) if drop else p)
    return out


# ── 쪽 안의 끊긴 줄 잇기 (postprocessor.join_broken_lines) ───────────────────────
_PARA_START = re.compile(r'^[가-힣A-Za-z0-9\"\'\(「『《]')


def join_broken_lines(text: str) -> str:
    """같은 단락에 속하는 물리적 줄들을 공백으로 이어 붙인다."""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        if not cur.strip():
            out.append(cur)
            i += 1
            continue
        while i + 1 < len(lines):
            nxt = lines[i + 1]
            if not nxt.strip():
                break
            if _should_join(cur, nxt):
                cur = cur.rstrip() + ' ' + nxt.lstrip()
                i += 1
            else:
                break
        out.append(cur)
        i += 1
    return '\n'.join(out)


def _should_join(cur: str, nxt: str) -> bool:
    cur_strip = cur.strip()
    nxt_strip = nxt.strip()
    if not cur_strip or not nxt_strip:
        return False
    if len(cur_strip) < 10:              # 짧은 줄은 제목/레이블
        return False
    if _SENT_END.search(cur_strip):
        return False
    if re.match(r'^\d+[.．]\s', nxt_strip):   # 목록 항목
        return False
    if nxt.startswith(('  ', '\t', '　')):     # 들여쓰기 = 새 단락
        return False
    return bool(_PARA_START.match(nxt_strip))


# ── 쪽 경계에서 끊긴 문장 잇기 (text_cleaner.rejoin_split_sentences) ─────────────
# 붙이면 안 되는 1음절 단어 — 관형사·대명사·부사 + 의존명사(수·것…)
_EXCLUDE_1CHAR = {"이", "그", "저", "한", "새", "첫", "옛", "딴", "온", "몇", "늘", "더", "덜", "참",
                  "수", "것", "줄", "데", "바", "뿐", "듯", "채", "때", "등", "및", "또", "곧",
                  "잘", "못", "안", "좀", "다", "꼭", "왜"}
# 뒤 어절 '전체'가 조사일 때만 결합
_JOSA_WORDS = {"을", "를", "은", "는", "이", "가", "의", "에", "와", "과", "로", "도", "만", "께",
               "으로", "에서", "에게", "에는", "에도", "까지", "부터", "처럼", "보다", "께서",
               "한테", "이나", "나", "랑", "이랑", "조차", "마저", "으로는", "에서는", "와는", "과는"}
_SPLIT_MIN_LEN = 60     # 이보다 짧은 줄은 제목·시행일 수 있어 잇지 않는다
_TOC_LIKE_RE = re.compile(r'\s\d{1,4}$| · ')


def _glue(before: str, after: str) -> str:
    """끊긴 두 어절 사이에 넣을 문자 — 한 단어가 잘린 것이면 '', 아니면 ' '."""
    is_hangul = lambda c: "가" <= c <= "힣"
    if is_hangul(before[-1]) and is_hangul(after[0]):
        if len(before) == 1 and before not in _EXCLUDE_1CHAR:
            return ""
        if re.sub(r'[^가-힣]+$', '', after) in _JOSA_WORDS:
            return ""
    return " "


def rejoin_split_sentences(text: str) -> tuple[str, int]:
    """문장이 끝나지 않은 긴 줄과 이어지는 한글 줄을 한 문장으로 잇는다. (텍스트, 이은 수)."""
    lines = text.split("\n")
    out: list[str] = []
    joined = 0
    i = 0
    while i < len(lines):
        cur = lines[i]
        while True:
            j = i + 1
            if j < len(lines) and not lines[j].strip():
                j += 1                      # 빈 줄 하나(문단 끊김)까지는 건너 본다
            if j >= len(lines):
                break
            s, nxt = cur.rstrip(), lines[j].strip()
            if (len(s) < _SPLIT_MIN_LEN or _SENT_END.search(s) or _TOC_LIKE_RE.search(s)
                    or not re.match(r'[가-힣]', nxt)
                    or (len(nxt) < 30 and not _SENT_END.search(nxt))):   # 다음 줄이 제목 꼴
                break
            cur = s + _glue(s.split()[-1], nxt.split()[0]) + nxt
            joined += 1
            i = j
        out.append(cur)
        i += 1
    return "\n".join(out), joined


# ── 러닝헤더 제거 (text_cleaner.strip_running_headers) ──────────────────────────
# 쪽 맨 첫/끝 줄만, 같은 줄이 _HEADER_MIN_PAGES쪽 이상 반복될 때만, 첫 등장(표제·장 시작)은 보존.
_HEADER_MIN_PAGES = 5
_HEADER_MAX_LEN = 40
_PAGE_NO_RE = re.compile(r'^\d{1,4}\s+|\s+\d{1,4}$')
# '머리말 | 029'·'028 | 피터 드러커의 매니지먼트' 꼴 쪽 꼬리말
_FOOTER_RE = re.compile(r'^(?:[^|\d][^|]{0,39}\|\s*\d{1,4}|\d{1,4}\s*\|[^|]{1,40})$')


def _header_key(line: str) -> str | None:
    k = re.sub(r'\s+', ' ', line.strip())
    stripped = _PAGE_NO_RE.sub('', k).strip()
    if stripped != k and ' ' in stripped:
        k = stripped
    if not (2 <= len(k) <= _HEADER_MAX_LEN) or k.startswith('['):
        return None
    if not re.search(r'[가-힣A-Za-z]{2}', k):
        return None
    return k


def _edge_indices(lines: list[str]) -> list[int]:
    idx = [i for i, l in enumerate(lines) if l.strip()]
    return sorted({idx[0], idx[-1]}) if idx else []


def strip_running_headers(pages: list[str]) -> list[str]:
    """쪽 맨 첫/끝 줄에 _HEADER_MIN_PAGES쪽 이상 반복되는 줄(러닝헤더)과 '제목 | 쪽' 꼬리말을 제거."""
    split = [p.splitlines() for p in pages]
    seen: dict[str, set[int]] = {}
    for n, lines in enumerate(split):
        for i in _edge_indices(lines):
            k = _header_key(lines[i])
            if k:
                seen.setdefault(k, set()).add(n)
    headers = {k: min(ns) for k, ns in seen.items() if len(ns) >= _HEADER_MIN_PAGES}
    out = []
    for n, lines in enumerate(split):
        drop = {i for i in _edge_indices(lines)
                if ((k := _header_key(lines[i])) in headers and headers[k] != n)
                or _FOOTER_RE.match(lines[i].strip())}
        out.append("\n".join(l for i, l in enumerate(lines) if i not in drop)
                   if drop else pages[n])
    return out


# ── 차례·판권·도서 광고 제거 (text_cleaner.strip_book_matter) ───────────────────
_TOC_HEAD_RE = re.compile(r'^(?:상권|중권|하권|전권|상|하)?\s*(?:차\s?례|목\s?차|contents)\s*$', re.I)
_SENT_LINE_RE = re.compile(r'[.?!][”’"\')」』]*\s*$')
_COLOPHON_RE = re.compile(r'ISBN|펴낸곳|펴낸이|발행처|발행인|출판등록|책값|재사용하려면|교환해\s*드립니다|KI신서|\d\s*판\s*\d\s*쇄')
_AD_RE = re.compile(r'\d{2,4}\s*[쪽면]\s*[|｜]\s*[\d,]{4,}\s*원')   # '296쪽 | 14,000원' 도서 광고
_COLOPHON_PAGE_MIN_HITS = 3     # 한 쪽에 서로 다른 표지가 이만큼이면 판권 쪽으로 본다
_COLOPHON_MAX_PAGES = 3
_MATTER_FRONT_MAX_PAGES = 40
_MATTER_FRONT_RATIO = 0.15


def _is_sentence_line(line: str) -> bool:
    s = line.strip()
    return len(s) >= 20 and bool(_SENT_LINE_RE.search(s))


def _paragraph_bounds(lines: list[str]) -> list[tuple[int, int]]:
    """줄 리스트를 빈 줄 기준 문단 (시작, 끝+1) 목록으로."""
    out, start = [], None
    for i, l in enumerate(lines):
        if l.strip():
            if start is None:
                start = i
        elif start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(lines)))
    return out


def _front_window(n: int) -> int:
    return min(n, _MATTER_FRONT_MAX_PAGES, max(10, int(n * _MATTER_FRONT_RATIO)))


def _drop_toc(pages: list[str], notes: list[str]) -> list[str]:
    """선두 구간에서 '차례' 머리글부터 문장이 나오기 전까지를 제거."""
    n = len(pages)
    window = _front_window(n)
    out = list(pages)
    i = 0
    while i < window:
        lines = out[i].splitlines()
        head = next((k for k, l in enumerate(lines) if _TOC_HEAD_RE.match(l.strip())), None)
        if head is None:
            i += 1
            continue
        first = lines[head].strip()
        kept_before = "\n".join(lines[:head]).rstrip()
        tail = lines[head:]
        sent = next((k for k, l in enumerate(tail) if _is_sentence_line(l)), None)
        if sent is None:
            kept_after = ""
            toc_end = None
        else:
            para = next(((a, b) for a, b in _paragraph_bounds(tail) if a <= sent < b), (sent, sent))
            # OCR 결과엔 문단 사이 빈 줄이 없어 문단이 차례 머리글부터 시작할 수 있다 → 문장 줄부터 남김
            start = para[0] if para[0] > 0 else sent
            kept_after = "\n".join(tail[start:]).strip()
            toc_end = i
        out[i] = "\n".join(x for x in (kept_before, kept_after) if x)
        last = i
        if toc_end is None:   # 다음 쪽부터 문장 줄이 나오는 쪽 직전까지 차례로 간주
            j = i + 1
            while j < n and not any(_is_sentence_line(l) for l in out[j].splitlines()) \
                    and re.search(r'\d', out[j]):
                out[j] = ""
                last = j
                j += 1
        notes.append(f"차례 {i + 1}~{last + 1}번째 쪽('{first[:12]}…')")
        i = last + 1
    return [p for p in out if p.strip()] if out != pages else pages


def _cut_colophon_block(text: str) -> str:
    """판권 표지가 든 첫 문단부터 마지막 문단까지 제거 — 같은 쪽의 일러두기·표제 등은 보존."""
    lines = text.splitlines()
    hit = [(a, b) for a, b in _paragraph_bounds(lines) if _COLOPHON_RE.search("\n".join(lines[a:b]))]
    return "\n".join(lines[:hit[0][0]] + lines[hit[-1][1]:]).strip()


def _drop_colophon(pages: list[str], notes: list[str]) -> list[str]:
    """선두·말미 쪽에서 판권(간기)과 다른 책 광고를 제거."""
    out = list(pages)
    n = len(out)
    found: dict[str, list[int]] = {}
    targets = sorted(set(range(_front_window(n))) | set(range(max(0, n - _COLOPHON_MAX_PAGES), n)))
    for idx in targets:
        text = out[idx]
        if _AD_RE.search(text):
            out[idx] = ""
            found.setdefault("도서 광고", []).append(idx + 1)
        elif len({m.group(0) for m in _COLOPHON_RE.finditer(text)}) >= _COLOPHON_PAGE_MIN_HITS:
            out[idx] = _cut_colophon_block(text)
            found.setdefault("판권", []).append(idx + 1)
    # 마지막 쪽: 표지가 적어도 끝 문단들이 판권이면 제거(그 앞 주석 등은 보존)
    last = max((i for i, p in enumerate(out) if p.strip()), default=None)
    if last is not None and last >= n - _COLOPHON_MAX_PAGES:
        lines = out[last].splitlines()
        cut = len(lines)
        for a, b in reversed(_paragraph_bounds(lines)):
            if _COLOPHON_RE.search("\n".join(lines[a:b])):
                cut = a
            else:
                break
        if cut < len(lines):
            out[last] = "\n".join(lines[:cut]).rstrip()
            found.setdefault("판권", []).append(last + 1)
    for kind, idxs in found.items():
        notes.append(f"{kind} {', '.join(str(i) for i in sorted(set(idxs)))}번째 쪽")
    # 빈 쪽은 여기서 빼지 않는다 — 앞쪽 판권을 지운 뒤 빼면 다음 차례 보고의 쪽 번호가 당겨진다
    return out if found else pages


def strip_book_matter(pages: list[str]) -> tuple[list[str], list[str]]:
    """차례(선두)·판권(말미)을 규칙으로 제거. (남은 쪽들, 사람용 보고 줄들) 반환."""
    notes: list[str] = []
    pages = _drop_colophon(pages, notes)   # 말미 먼저 — 쪽 번호가 입력 기준으로 유지된다
    pages = _drop_toc(pages, notes)
    return [p for p in pages if p.strip()], notes
