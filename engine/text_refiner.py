"""문장 정제·부속물 삭제 (규칙 기반, 오프라인) — '일반 텍스트' 모드 결과용.

01_OCR_스캔파일텍스트추출프로그램 의 core/text_cleaner.py · core/postprocessor.py 에서
LLM(Gemini) 없이 동작하는 결정론 단계만 옮겨 왔다. 함수 본문은 원본과 같게 유지한다
(원본 실측·회귀 테스트로 다듬어진 규칙이므로).

- 문장 정제: 쪽번호·반복 머리글/꼬리말 제거, 쪽 안의 끊긴 줄 잇기, 쪽 경계에서 끊긴 문장 잇기
- 부속물 삭제: 선두의 차례, 선두·말미의 판권(간기)·도서 광고 제거
  (표제지·헌사·약력처럼 형태가 뚜렷하지 않은 앞부속은 원본에서도 LLM이 처리 — 여기선 남는다)
- OCR 잡음·단락 규칙(noise_rules.py): 01 의 core/noise_rules.py 를 바이트 그대로 복사(2026-10-16, 재동기화 2026-10-20).
  01 '비용 우선(내장 정제규칙)' 경로와 같은 순서로 부른다 — 고칠 때는 01 원본을 고치고 다시 복사한다.
- 줄바꿈·단락(2026-10-20, 01 의 10-17~10-19 수정 이식): 쪽 경계 표지로 쪽 경계 잇기(post_process_pagebreaks),
  항목 줄(1. ② 가. 제1조)은 앞 줄에 잇지 않기, 낱말 중간에서 갈린 줄은 붙여 잇기(data/wrap_nospace.txt),
  쪽 첫 줄 제목은 앞 문단에 잇지 않기, 단락 사이 빈 줄 하나로 통일(unify_paragraph_breaks).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import korean_corrector
import noise_rules

# 문장이 '깔끔히' 끝났는지 판단
_SENT_END = re.compile(r'[.!?…。！？”’"\'\)\]》」』]\s*$')
PAGEBREAK = "⟨PAGEBREAK⟩"
_WRAP_TABLE = Path(__file__).resolve().parent / "data" / "wrap_nospace.txt"   # 01 core/data 와 같은 파일


def refine_pages(pages: list[str], drop_matter: bool = False, structure=None) -> tuple[str, list[str]]:
    """쪽별 텍스트 → (정제된 전체 본문, 사람용 보고 줄들).
    structure(pages) -> pages: 쪽번호·머리글을 뗀 뒤, 줄 잇기 전에 부르는 구조 정비
    (book_layout.mark_headings — 차례 제목을 '# 제목' 줄로). 제목 줄은 아래 줄 잇기에서 붙이지 않는다."""
    # 빈 쪽도 남겨 두어 보고의 쪽 번호가 PDF 쪽과 맞게
    # 잡음 줄·기호·전각 부호·깨진 번호·끊긴 영어 줄 (01 noise_rules — LLM 없이 거르는 OCR 잡음)
    pages = [remove_noise_lines(noise_rules.clean_page(normalize_chars((p or "").strip()))) for p in pages]
    notes: list[str] = []
    if drop_matter:   # 빈 쪽을 남겨 이후 보고(잡음 쪽)의 쪽 번호도 PDF 쪽과 맞게
        pages = noise_rules.drop_book_matter_rules(pages, notes)   # 표지·저자 소개·광고·저작권·차례
        pages, matter = strip_book_matter(pages, keep_empty=True)
        notes += matter
    pages = strip_page_numbers(pages)
    pages = strip_running_headers(pages)
    pages, n_hdr = noise_rules.strip_fuzzy_headers(pages)
    if n_hdr:
        notes.append(f"깨져 읽힌 머리글 {n_hdr}곳 자동 제거")
    if structure is not None:
        pages = structure(pages)
    # 출처 표기 명언집: 줄 잇기 전에 '명언 → 저자 출처' 그룹 경계를 고정(번호를 못 읽은 항목 포함)
    pages, _ = noise_rules.group_attributed_quotes(pages)
    pages = drop_noise_pages(pages, notes)
    # 01 _rules_clean 과 같은 순서: 쪽을 경계 표지로 이어 쪽 경계에서 끊긴 문장·어절을 먼저 잇고, 그다음 줄 잇기.
    # 예전엔 쪽을 빈 줄로만 이어 짧게 끊긴 문장('…결단을 반복' / 다음 쪽 '하고 있는 것은')이 단락으로 갈렸다
    text = ("\n" + PAGEBREAK + "\n").join(fix_hyphenated_words(p) for p in pages if p.strip())
    text = join_broken_lines(post_process_pagebreaks(text))
    text, _ = rejoin_split_sentences(text)
    text = noise_rules.finish_groups(text)              # 그룹 경계 표지 → 빈 줄(줄 잇기가 끝난 뒤)
    text, _ = noise_rules.repair_quote_brackets(text)   # 인용 괄호 〈〉「」『』 짝 복원
    text, _ = noise_rules.split_language_lines(text)    # 한 줄에 붙은 한글·영어 문장 가르기
    if noise_rules.is_numbered_book(text):              # 번호 붙은 항목 책(명언집)만 — 일반 문서 표·목록 보호
        text, _ = noise_rules.redistribute_number_stacks(text)
        text, _ = noise_rules.paragraph_entries(text)
    # 줄을 이은 뒤 문서 전체를 근거로 쪼개진 어절 결합 ('사 실은'→'사실은') — 파일형식변환기_MD 처럼
    # 교정을 줄 잇기 뒤에 돌린다. OCR 줄 단위 교정은 줄 사이에서 끊긴 어절을 볼 수 없다.
    text = korean_corrector.fix_split_tokens(text, document_corpus=text)
    text = fix_punctuation_spacing(text)
    text = unify_paragraph_breaks(text)                 # 단락 사이는 빈 줄 하나(C-08) — 01 과 같게
    # noise_rules 보고의 '원문 사본' 안내는 01 전용(MyOCR 는 사본을 남기지 않는다 — '문장 정제'를 끄면 원문)
    notes = [n.replace(" (정제 전 원문 사본에서 확인 가능)", "") for n in notes]
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
    # '즉,'·'단,' 처럼 한 글자 말 + 문장부호인 줄은 내용이라 남긴다 (정제본 실측)
    return "\n".join(l for l in text.splitlines()
                     if not l.strip() or len(re.sub(r"[^\w가-힣]", "", l)) >= 2
                     or re.fullmatch(r"[가-힣][,.:]", l.strip()))


def fix_hyphenated_words(text: str) -> str:
    """줄 끝 하이픈으로 쪼개진 영단어 복원 ('infor-\\nmation'→'information').
    원본의 한글 규칙('효율적-\\n으로')은 옮기지 않았다 — 한국어는 하이픈으로 어절을 끊지 않아
    줄 끝 하이픈은 대개 진짜 하이픈('서울-\\n부산')이다."""
    return re.sub(r"([a-zA-Z])-\n\s*([a-z])", r"\1\2", text)


def fix_punctuation_spacing(text: str) -> str:
    """구두점 앞 공백 제거, 한글 앞 구두점 뒤 띄움, 괄호 안쪽 공백 제거.
    원본은 구두점 뒤 영문까지 띄워 'www.naver.com'·'e.g.' 를 망가뜨려 한글 앞만 띄운다."""
    text = re.sub(r"(?<=\S)[ \t]+([.,!?])(?=\s|$|[가-힣])", r"\1", text, flags=re.M)
    # 영문 머리글자 뒤 마침표('C.의'·'U.S.의')는 띄우지 않는다 (정제본 실측)
    text = re.sub(r"(?<![A-Za-z])([.])([가-힣])|([,!?])([가-힣])",
                  lambda m: f"{m.group(1) or m.group(3)} {m.group(2) or m.group(4)}", text)
    text = re.sub(r"([(\[])[ \t]+", r"\1", text)
    return re.sub(r"[ \t]+([)\]])", r"\1", text)


# ── OCR 잡음 쪽 ─────────────────────────────────────────────────────────────
# 실측(1분의사결정 96쪽): '…해낼 만큼' ⟨'인걸' 두 글자뿐인 쪽⟩ '의 인격을…' — 그림·얼룩을 읽은 쪽이
# 문장 한가운데 끼어든다. 글자 수만으로는 '서문'·'PART 1' 같은 제목 쪽과 구분되지 않으므로
# 문장이 그 쪽을 건너 실제로 이어질 때 — 앞 쪽은 문장이 끝나지 않았고 다음 쪽은 단어 첫머리에 올 수 없는
# 어미·조사('의 인격을')로 시작 — 만 잡음으로 본다. 앞 쪽 끝만 보면 서명('스펜서 존슨') 뒤 장 제목 쪽을 지운다.
_NOISE_PAGE_MAX = 15        # 의미 있는 글자(한글·영문·숫자)가 이보다 적은 쪽만 후보


def drop_noise_pages(pages: list[str], notes: list[str]) -> list[str]:
    """문장 중간에 끼어든 짧은 잡음 쪽을 비운다(쪽 번호 유지). 지운 쪽은 notes 에 보고."""
    out = list(pages)
    dropped = []
    for i, p in enumerate(out):
        s = p.strip()
        if not s or len(re.findall(r'[가-힣A-Za-z0-9]', s)) >= _NOISE_PAGE_MAX or _SENT_END.search(s):
            continue
        prev = next((q.rstrip() for q in reversed(out[:i]) if q.strip()), "")
        nxt = next((q for q in out[i + 1:] if q.strip()), "")
        if prev and nxt and not _SENT_END.search(prev) and _starts_bound(nxt):
            dropped.append(f"{i + 1}('{' '.join(s.split())[:10]}')")
            out[i] = ""
    if dropped:
        notes.append(f"OCR 잡음 쪽 {', '.join(dropped)}")
    return out


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


# 줄이 오른쪽 끝까지 찼다고 볼 비율 — 쪽에서 흔한 줄 길이(중앙값)의 이만큼 이상이어야 다음 줄과 잇는다.
# 정제본 23권 실측: 길이만 보면 제목('목표는 완전한 이해다')·표 행('국내 극장 4,928,000')을 본문에 붙였다
_FULL_LINE_RATIO = 0.75
# 항목 시작(글머리 기호·일련번호·조항) — 이 줄은 앞 줄에 잇지 않는다. 쪽 경계 잇기도 같이 쓴다(01 postprocessor).
# '1.5배'·'가나다'·'제3조에' 처럼 뒤에 공백·괄호가 없으면 항목 표시가 아니다.
ITEM_START_RE = re.compile(
    r'^(?:[-–—•·*▶▷►>»※□■○●◆◇▪◦☞➤✓]'
    r'|\(?\d{1,3}[.．)]\s'                          # 1. 2) (3)
    r'|[①-⑳㉠-㉭㈀-㈍]'                              # ① ㉠ ㈀
    r'|제\s?\d{1,3}\s?[조장절항편부관](?:\s|\(|$))')   # 제1조 제2장
# 한글 항목(가. 나) (다)) — 줄 끝에서 잘린 '…운용되었|다. 그러나' 와 모양이 같아,
# 앞이나 뒤 글자도 다른 줄 머리에 항목으로 나올 때만 항목으로 본다(목록은 두 개 이상이다)
_KO_ITEM_RE = re.compile(r'^\(?([가나다라마바사아자차카타파하])[.)]\s')
_KO_SEQ = "가나다라마바사아자차카타파하"
# 마지막 줄의 쪽 꼬리말('2장 결단 19') — 본문 줄에 붙지 않게
_PAGE_FOOTER_LINE = re.compile(r'^(?:\d{1,3}\s*)?\d{1,2}\s?[장부]\s?\S.{0,35}?\s?\d{1,3}$|^\d{1,3}\s*\d{1,2}\s?[장부]\s?\S.{0,35}$')


def item_letters(lines) -> frozenset[str]:
    """줄 머리에 한글 항목 표시로 나온 글자들 — is_item_start 에 넘긴다."""
    return frozenset(m[1] for l in lines if (m := _KO_ITEM_RE.match(l.lstrip())))


def is_item_start(line: str, letters: frozenset[str] = frozenset()) -> bool:
    """항목 시작 줄인가(글머리 기호·일련번호·조항·한글 항목)."""
    s = line.lstrip()
    if ITEM_START_RE.match(s):
        return True
    m = _KO_ITEM_RE.match(s)
    if not m:
        return False
    k = _KO_SEQ.index(m[1])
    return bool(letters & {_KO_SEQ[k - 1] if k else "", _KO_SEQ[k + 1] if k + 1 < len(_KO_SEQ) else ""})


def join_broken_lines(text: str) -> str:
    """같은 단락에 속하는 물리적 줄들을 이어 붙인다(01 postprocessor.join_broken_lines).
    오른쪽 끝까지 찬 줄(조판상 줄바꿈)만 잇고, 짧은 줄(제목·표 행)과 항목 줄 앞에서는 멈춘다.
    낱말 중간에서 갈린 자리('운용되었|다')는 붙이고 나머지는 띄운다(_join_wrapped)."""
    lines = text.splitlines()
    lens = sorted(len(l.strip()) for l in lines if l.strip())
    full = lens[len(lens) // 2] * _FULL_LINE_RATIO if lens else 0
    letters = item_letters(lines)
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
            if not nxt.strip() or _MD_HEADING_RE.match(cur) or _MD_HEADING_RE.match(nxt):
                break
            # 지금까지 이은 줄이 아니라 '현재 물리적 줄'(lines[i])이 끝까지 찼는지를 본다
            last = not any(l.strip() for l in lines[i + 2:])
            if (len(lines[i].strip()) >= full and not is_item_start(nxt, letters)
                    and not (last and _PAGE_FOOTER_LINE.match(nxt.strip()))
                    and _should_join(cur, nxt)):
                cur = _join_wrapped(cur, nxt)
                i += 1
            else:
                break
        out.append(cur)
        i += 1
    return '\n'.join(out)


@lru_cache(maxsize=1)
def _wrap_nospace() -> frozenset[str]:
    """붙여 잇는 한글 쌍 표. 파일이 없으면 빈 표(예전처럼 늘 띄운다)."""
    try:
        return frozenset(_WRAP_TABLE.read_text(encoding="utf-8").split())
    except OSError:
        return frozenset()


def _join_wrapped(cur: str, nxt: str) -> str:
    """줄바꿈으로 갈라진 두 줄을 잇는다 — 낱말 중간에서 잘린 자리('운용되었|다')면 붙이고, 아니면 띄운다."""
    cur, nxt = cur.rstrip(), nxt.lstrip()
    if cur and nxt and (cur[-1] + nxt[0]) in _wrap_nospace():
        return cur + nxt
    return cur + ' ' + nxt


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
# 단어 첫머리에 올 수 없는 어미·조사 — 다음 줄이 이걸로 시작하면 앞 줄이 짧아도(제목일 수 없으니) 잇는다.
# 실측: '…잘못을 발견하' ⟨쪽 경계⟩ '며, 아무도…'. '이·가·서·만·도' 는 '이 책'·'서 있다'·'만 원' 처럼
# 독립 단어로도 쓰여 제외한다
_BOUND_START = {"며", "으며", "니", "으니", "니까", "지만", "면서", "도록", "거나", "는데", "은데", "으면",
                "는", "은", "을", "를", "에", "의", "에서", "에게", "으로", "로", "께서",
                "었다", "았다", "였다", "했다", "한다", "된다", "합니다", "입니다", "습니다", "니다"}


def _starts_bound(line: str) -> bool:
    # 첫 토큰의 앞쪽 한글만 본다 — 전각 쉼표가 반각으로 바뀌며 '며,아무도' 처럼 붙어 오는 경우가 있다
    m = re.match(r'[가-힣]+', line.strip())
    return bool(m) and m.group(0) in _BOUND_START


def _glue(before: str, after: str) -> str:
    """끊긴 두 어절 사이에 넣을 문자 — 한 단어가 잘린 것이면 '', 아니면 ' '."""
    is_hangul = lambda c: "가" <= c <= "힣"
    if is_hangul(before[-1]) and is_hangul(after[0]):
        if len(before) == 1 and before not in _EXCLUDE_1CHAR:
            return ""
        if re.sub(r'[^가-힣]+$', '', after) in _JOSA_WORDS:
            return ""
    return " "


_MD_HEADING_RE = re.compile(r'^#{1,6} \S')


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
            if _MD_HEADING_RE.match(s) or _MD_HEADING_RE.match(nxt):   # 장·절 제목 줄(book_layout)
                break
            bound =bool(s) and re.search(r'[가-힣]$', s) is not None and _starts_bound(nxt)
            if bound:   # 끊긴 어미·조사 — 한 단어가 잘린 것이니 공백 없이 잇는다
                cur = s + nxt
                joined += 1
                i = j
                continue
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


# ── 쪽 경계 잇기·단락 통일 (01 text_cleaner.post_process_pagebreaks · unify_paragraph_breaks) ─────────
_HEADING_MAX = 25                     # 이보다 긴 쪽 첫 줄은 앞 쪽에서 이어지는 본문 줄로 본다
_HEADING_TAIL_STRIP = '"\'”’)]」』》'


def _is_heading_line(rest: str) -> bool:
    """쪽 첫 줄이 제목·장 번호인가 — 짧고, 문장 끝(. ? ! … , 다 요 까)으로 끝나지 않고, 아래에 줄이 더 있다.
    짧은 이음 줄('고 하는 데에는 다른이유가 있다.', '져나갈궁리를 한다')은 문장 끝으로 끝나 앞 쪽에 잇는다."""
    first, _, more = rest.partition("\n")
    tail = first.strip().rstrip(_HEADING_TAIL_STRIP)
    return (0 < len(first.strip()) <= _HEADING_MAX and bool(tail) and tail[-1] not in ".!?。…,다요까"
            and bool(more.strip()))


def post_process_pagebreaks(text: str) -> str:
    """⟨PAGEBREAK⟩ 경계에서 잘린 어절·문장을 잇고 표지를 지운다. 앞 쪽이 문장으로 끝났으면 단락 경계(빈 줄).
    다음 쪽이 장·절 제목(MD)·항목(2. ② 나. 제2조)·제목 꼴 줄로 시작하면 잇지 않는다."""
    if not text:
        return ""
    letters = item_letters(text.replace(PAGEBREAK, "\n").splitlines())

    def _repl(match):
        before, ws_before, ws_after, after = match.groups()
        line_start = match.string.rfind("\n", 0, match.start()) + 1
        if re.fullmatch(r'#{1,6}', after) or _MD_HEADING_RE.match(match.string[line_start:match.end(1)]):
            return before + "\n\n" + after
        if "\n" in ws_before + ws_after and is_item_start(match.string[match.start(4):], letters):
            return before + "\n" + after
        if "\n" in ws_before + ws_after and (ws_after.endswith("  ")
                                             or _is_heading_line(match.string[match.start(4):])):
            return before + "\n\n" + after
        if _SENT_END.search(before):
            return before + ("\n\n" if "\n" in ws_before + ws_after else " ") + after
        return before + _glue(before, after) + after

    processed = re.sub(r'(\S+)(\s*)⟨PAGEBREAK⟩(\s*)(\S+)', _repl, text)
    return processed.replace(PAGEBREAK, "")


# 앞 줄에 붙여 두는 줄 — 명언 저자 줄('- 벤저민 프랭클린…')·글머리 목록
_ATTACHED_LINE = re.compile(r"[-•·▶■□○●◆※]\s")


def unify_paragraph_breaks(text: str) -> str:
    """단락 사이를 빈 줄 하나로 통일한다(C-08 '단락 = 빈 줄'). 줄 잇기가 끝난 뒤 남은 줄바꿈은 단락 경계인데
    쪽 안에서는 줄바꿈 하나·쪽 경계에서는 빈 줄로 섞여 있었다(MyOCR 1분의사결정: 빈 줄 129·줄바꿈 하나 1,089).
    글머리(- •)로 시작하는 줄은 앞 줄과 한 덩어리로 둔다 — 명언과 저자 줄이 갈라지지 않게."""
    out: list[str] = []
    for line in (l.strip() for l in text.split("\n")):
        if not line:
            continue
        if out:
            out.append("\n" if _ATTACHED_LINE.match(line) else "\n\n")
        out.append(line)
    return "".join(out)


# ── 러닝헤더 제거 (text_cleaner.strip_running_headers) ──────────────────────────
# 쪽 맨 첫/끝 줄만, 같은 줄이 _HEADER_MIN_PAGES쪽 이상 반복될 때만, 첫 등장(표제·장 시작)은 보존.
_HEADER_MIN_PAGES = 5
_HEADER_MAX_LEN = 40
_PAGE_NO_RE = re.compile(r'^\d{1,4}\s+|\s+\d{1,4}$')
# '머리말 | 029'·'028 | 피터 드러커의 매니지먼트' 꼴 쪽 꼬리말
_FOOTER_RE = re.compile(r'^(?:[^|\d][^|]{0,39}\|\s*\d{1,4}|\d{1,4}\s*\|[^|]{1,40})$')


# OCR 이 같은 머리글을 쪽마다 다르게 읽는 경우(실측: 1분의사결정 165쪽) —
#   장 번호가 깨짐('IV.'·'n.'·'in,'), 쪽번호가 깨지거나 붙음('6?'·'6i'·'찾는다23'), 띄어쓰기가 다름('머리를써라')
_SECTION_PREFIX_RE = re.compile(r'^(?:[IVXivxnl]{1,4}|\d{1,3})?\s*[.,]\s*')     # 'IV.'·'I .'·'n.'·'in,'
# 쪽번호: 숫자가 섞인 꼬리('6?'·'6i'·'찾는다23'), 또는 한글 뒤 숫자 닮은 글자뿐인 꼬리('51'→'si')
_GARBLED_PAGE_NO_RE = re.compile(r'\s*[\dIlOoi?!|]*\d[\dIlOoi?!|]*$|(?<=[가-힣])\s+[IlOoiSsZz?!|]{1,3}$')


def _header_key(line: str) -> str | None:
    k = re.sub(r'\s+', ' ', line.strip())
    stripped = _PAGE_NO_RE.sub('', k).strip()
    if stripped != k and ' ' in stripped:
        k = stripped
    # 장 번호 표시가 앞에 있으면 깨진·붙은 쪽번호까지 떼고 공백 없이 비교한다.
    # 장 번호 표시가 없는 '누가복음 5'·'Chapter 1' 은 위 규칙대로 묶지 않는다(장 머리글 보호)
    m = _SECTION_PREFIX_RE.match(k)
    if m and m.end() > 0:
        core = _GARBLED_PAGE_NO_RE.sub('', k[m.end():]).strip()
        if len(re.findall(r'[가-힣A-Za-z]', core)) >= 4:
            k = '§' + re.sub(r'\s+', '', core)
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
    return out   # 빈 쪽은 strip_book_matter 가 걸러낸다(쪽 번호 유지가 필요하면 남긴다)


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


def strip_book_matter(pages: list[str], keep_empty: bool = False) -> tuple[list[str], list[str]]:
    """차례(선두)·판권(말미)을 규칙으로 제거. (남은 쪽들, 사람용 보고 줄들) 반환.
    keep_empty=True 면 지운 쪽을 빈 문자열로 남겨 쪽 번호(인덱스)를 유지한다."""
    notes: list[str] = []
    pages = _drop_colophon(pages, notes)   # 말미 먼저 — 쪽 번호가 입력 기준으로 유지된다
    pages = _drop_toc(pages, notes)
    return (pages if keep_empty else [p for p in pages if p.strip()]), notes
