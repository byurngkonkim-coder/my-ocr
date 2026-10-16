"""비용 우선(내장 정제규칙) 경로의 OCR 잡음·단락 규칙 — 2026-10-16.

실측(01_입력 명언집 5권, RapidOCR → _rules_clean 결과 02_결과):
  잡음 줄 990 · 잡음 토큰 1,918 · 전각 부호 3,410 · 깨진 머리글 잔류 725 ·
  한 단어씩 끊긴 영어 줄 194, 그리고 번호 붙은 명언 항목이 빈 줄 없이 이어지거나
  한 줄에 다음 항목이 붙어(… 기술교 408 | 시간을…) 단락 구분이 거의 없었다.

원칙: 모양이 뚜렷한 잡음만 지운다. 본문일 수 있으면 남긴다(원문 사본은 storage 에 보존).
  - 쪽 단위(clean_page): 전각→반각, 잡음 토큰, 깨진 항목 번호 복원, 잡음 줄, 영어 단어 줄 잇기
  - 문서 단위(strip_fuzzy_headers): 쪽마다 다르게 깨져 읽힌 러닝헤더('실라콘繼리 천재醫의…')
  - 문서 단위(paragraph_entries): 연속 번호(n, n+1, …) 항목마다 단락(빈 줄) 복원
"""
from __future__ import annotations

import re
from bisect import bisect_left as _bisect
from collections import Counter

# ── 쪽 단위 ────────────────────────────────────────────────────────────────────
_FULLWIDTH = str.maketrans({"，": ",", "：": ":", "；": ";", "（": "(", "）": ")",
                            "？": "?", "！": "!", "［": "[", "］": "]", "｜": "|",
                            "、": ",", "－": "-", "～": "~", "＞": ">", "＜": "<",
                            "。": ".", "＂": '"', "＇": "'"})

# 옛한글 자모(U+1100~11FF)는 현대 한국어 본문에 오지 않는다 — OCR 이 획을 읽은 것('ᄀ 하지만', 'ᄂf 이다')
_JAMO_TOKEN_RE = re.compile(r"(?<!\S)[ᄀ-ᇿ][A-Za-z]{0,2}(?!\S)")
_JAMO_CHAR_RE = re.compile(r"[ᄀ-ᇿ]")
# 장식 기호 덩어리('■■■', '•', '^', '«') — 토큰 전체가 기호일 때만
_SYMBOL_TOKEN_RE = re.compile(r"(?<!\S)[■□▪▫◆◇●○•^«»°◊※*]+(?!\S)")
# 단, 같은 줄에서 짝이 맞는 '«…»'는 책·작품 제목 표시라 본문('«대지»의 작가' — 하루명화하루명언 실측)
_PAIRED_GUIL_RE = re.compile(r"«[^«»\n]{1,40}»")
_SYMBOL_LEAD_RE = re.compile(r"(?<!\S)(?!«[^«»\n]{1,40}»)[■□▪▫◆◇●○•^«»]+(?=[가-힣A-Za-z“‘\"'(])")
# 스캔 밑줄·점선('______', '-------')
_RULE_RE = re.compile(r"[_\-–—―一]{5,}")
# 깨진 항목 번호: 'OO52'·'O457' → '0052'·'0457'. 숫자 두 자리 이상일 때만(산소 'O2' 등 본문 보호)
_O_NUM_RE = re.compile(r"(?<![\w])([0O]{1,3})(\d{2,3})(?![\w])")
# 띄어 읽힌 숫자: 0으로 시작하는 줄 머리 번호 '0 0 5 1' → '0051', 단위가 바로 뒤따르는 수
# '1 9 8 3 년'·'1 0 0 퍼센트'·'2 9 2년' 만 잇는다 — 표·척도의 '1 2 3 4 5 중'은 붙이지 않는다
_SPACED_NUM_RE = re.compile(
    r"(?m)^[ \t]*(0) (\d) (\d)(?: (\d))?(?![\w\d])"
    r"|(?<![\w\d])(\d) (\d)(?: (\d))?(?: (\d))?(?=\s*(?:년|퍼센트|%|명|개|원|배|세|달러|억|만)(?![가-힣]{2}))")

_ALLOWED_PUNCT = set(".,!?'\"“”‘’()[]<>〈〉『』「」《》:;-–—~·%/…|_+=&#@")


# 숫자만 있는 줄의 o·i 오인식('ooi3' → '0013', 'OO7O' → '0070') — 줄 전체가 번호이고 숫자가 하나
# 이상 섞였을 때만(영단어 오변환 방지)
_GARBLED_NUM_LINE_RE = re.compile(r"(?m)^[ \t]*((?=[0-9OoilI]*\d)[0-9Oo][0-9OoilI]{1,3})[ \t]*$")


def _fix_numbers(text: str) -> str:
    text = _GARBLED_NUM_LINE_RE.sub(
        lambda m: m.group(1).translate(str.maketrans("OoilI", "00111"))
        if re.search(r"\d", m.group(1)) else m.group(0), text)
    text = _O_NUM_RE.sub(lambda m: m.group(1).replace("O", "0") + m.group(2), text)
    return _SPACED_NUM_RE.sub(lambda m: "".join(g for g in m.groups() if g), text)


def is_garbage_line(line: str) -> bool:
    """이미지·장식을 읽은 잡음 줄인가. 의미 있는 토큰(한글 2음절↑·영문 3자↑)이 없거나,
    짧은 줄에서 허용 외 기호가 30% 이상이면 잡음. 숫자만 있는 줄(항목 번호·연도)은 남긴다."""
    s = line.strip()
    if not s:
        return False
    ns = re.sub(r"\s", "", _PAIRED_GUIL_RE.sub(lambda m: m.group(0)[1:-1], s))   # 짝 맞는 '«»'는 잡기호로 세지 않음
    if re.fullmatch(r"[\d\-.,:/()|]+", ns):
        return False
    # 의미 있는 토큰: 한글 2자·영문 3자, 숫자+단위('5월 9일'), 띄어 쓴 표 머리('합 계')
    if not (re.search(r"[가-힣]{2}|[A-Za-z]{3}", s) or _NUM_UNIT_RE.search(s) or _SPACED_HEADER_RE.match(s)):
        return True
    classical = bool(re.search(r"[一-鿿]{4,}", s))     # 한문 인용 줄 — 한자를 잡음 문자로 세지 않는다
    bad = sum(1 for c in ns if not (re.match(r"[가-힣A-Za-z0-9]", c) or c in _ALLOWED_PUNCT
                                     or (classical and _HANJA_RE.match(c))))
    return len(ns) <= 60 and bad / len(ns) >= 0.3


_KEEP_HANGUL = 6           # 한글이 이만큼 있는 줄은 본문 — 통째로 지우지 않고 양끝 잡음 토큰만 뗀다


# 토큰 판정에서 정상으로 보는 문자 — 디지털 문서의 표기('[표1]'·'1%만'·'㎝도'·'③PT가'·'것:'·'쿨∼한')를
# 잡음으로 지우던 것 보호(평가 도구 실측: DBR 기사 45개에서 0.1~0.6%p 손실)
_OK_CHAR_RE = re.compile(r"[가-힣A-Za-z0-9\s“”‘’\"'().,!?…:;<>〈〉《》「」『』\[\]{}~∼%㎝㎞㎏㎎℃·\-–—/&+①-⑳]")
# 숫자 뒤 단위어 — '5월 9일'·'2016년'·'제 4장'은 본문, '7혓'·'5못'·'2야' 같은 OCR 잡음은 아님
_UNIT = r"(?:년|월|일|시|분|초|명|개|원|장|권|호|쪽|면|층|등|위|번|차|세|배|대|회|주|만|억|천|백|가지|퍼센트|살|건|곳|종|점|단계|부|편|절)"
_NUM_UNIT_RE = re.compile(rf"\d[%㎝㎞㎏㎎℃]?{_UNIT}(?![가-힣])|\d{_UNIT}[이가은는을를의에도과와로]")
# 띄어 쓴 표 머리 낱말('합 계'·'의 견') — 이 목록만 본문으로 본다('돈 나' 같은 잡음 조각 제외)
_SPACED_HEADER_RE = re.compile(r"^(?:합 계|총 계|소 계|의 견|총 평|비 고|구 분|항 목|내 용|평 가|점 수|순 위|합 산)$")
# 본문 토큰 꼴: 한자 병기 '건(件)', 숫자+한글 '2016년'·'1%만', 동그라미 숫자 '③PT', 한자+조사 '甲이'
_CONTENT_TOKEN_RE = re.compile(rf"\([一-鿿]+\)|\d[%㎝㎞㎏㎎℃]?{_UNIT}|\d%[가-힣]|[①-⑳]|^[一-鿿]+[가-힣]{{1,2}}[.,:]?$")


def _is_junk_token(t: str) -> bool:
    """줄 끝 잡음 토큰: 한글이 없거나, 한글 한 글자에 잡기호가 붙은 것('휴^', '■가') — 한 글자 낱말
    ('그 사람은'의 '그')·정상 표기('[표1]', '것:')는 본문으로 둔다."""
    if re.search(r"[가-힣]{2}|[A-Za-z]{3}|^\d{1,4}[.,]?$", t) or _CONTENT_TOKEN_RE.search(t):
        return False
    if re.search(r"[가-힣]", t):
        return not all(_OK_CHAR_RE.match(c) for c in t)
    return True


_GOOD_TOKEN_RE = re.compile(r"[가-힣]{2}|[A-Za-z]{3}|^\d+[.,]?$")


def _low_word_quality(line: str) -> bool:
    """짧은 줄의 낱말 품질: 토큰 3개↑인데 제대로 된 낱말(한글 2자↑·영단어 3자↑·숫자)이 절반 미만
    ('- 네’ 기직포*. 드’,r' — 악당의명언 실측)."""
    # 구분자('| 우정의 끝'·'I 기본의 힘'의 '|'·'I')는 세지 않고, 기호 없는 한 글자 낱말('끝')은 정상 낱말로 본다
    toks = [t for t in line.split() if t not in ("|", "I", "l", "•", "·", "-", "—", "–", "/")
            and not re.fullmatch(r"\d+[.,]?", t)]
    # 숫자+단위('2016년'·'8월'·'206호(2016년')도 정상 낱말 — 기고자 표시 줄 보호
    good = sum(1 for t in toks if _GOOD_TOKEN_RE.search(t) or re.fullmatch(r"[가-힣][.,!?]?", t)
               or _NUM_UNIT_RE.search(t))
    return len(toks) >= 3 and good / len(toks) < 0.5


def _salvage(line: str) -> str:
    """품질이 낮은 짧은 줄에서 잡음 토큰만 떼고, 문장 종결형 낱말('무시당한다.', '낸거야!”')이 남으면
    그 줄을 살린다 — 문장 끝이 없는 조각('기직포*.')만 지운다(악당의명언 실측: 통째 삭제로 본문 손실)."""
    toks = [t for t in line.split() if not re.fullmatch(r"\d+[.,]?", t) and (
            _GOOD_TOKEN_RE.search(t) or (re.search(r"[가-힣]", t) and not _is_junk_token(t)))]
    # 문장 종결형 낱말이나 4음절↑ 한글 낱말('현실화되면', '골로가는')이 남으면 본문 조각으로 살린다
    if any(re.search(r"[가-힣][다요야까죠네라][.!?”\"’']*$", t) or re.search(r"[가-힣]{4,}", t) for t in toks):
        return " ".join(toks)
    return ""


def _edge_cut(seq: list[str]) -> int:
    """줄 머리에서 뗄 잡음 토큰 수. 첫 진짜 낱말 앞에 기호만 있는 토큰이 2개↑ 몰려 있으면 그 앞을 통째로
    ('휴/ ,' : < : : < 성노,…' → '성노,…'), 아니면 하나씩 잡음 판정('㎝도 안 되지만'·'<막 공부를'은 유지)."""
    strong = lambda t: re.search(r"[가-힣]{2}|[A-Za-z]{3}", t) or _CONTENT_TOKEN_RE.search(t)
    k = next((i for i, t in enumerate(seq) if strong(t)), len(seq))
    if sum(1 for t in seq[:k] if not re.search(r"[가-힣A-Za-z0-9]", t)) >= 2:
        return k
    n = 0
    while n < k and _is_junk_token(seq[n]):
        n += 1
    return n


def _trim_junk_edges(line: str) -> str:
    """'/1 r* xr \\ x - 나항상 선택의…' → '나항상 선택의…' (본문 앞뒤에 붙은 잡음 토큰 제거)."""
    toks = line.split()
    a = _edge_cut(toks)
    b = len(toks) - _edge_cut(toks[::-1])
    return " ".join(toks[a:b]) if a < b else ""


_HANJA_RE = re.compile(r"[一-鿿㐀-䶿々]")


def _is_noise_token(t: str) -> bool:
    """본문 줄 안에 섞인 잡음 토큰('가令:乂■아', '乂', '#법을')인가. 한자 병기('(溫故知新)')는 본문."""
    # 한문 인용·한자 병기는 본문: '患人之不知己…也).”', '건(件)', '사(意思)’가'
    if re.fullmatch(r"[(\[“\"‘']?[一-鿿]{2,}[)\]”\"’'.,]*", t) or _CONTENT_TOKEN_RE.search(t):
        return False
    if re.search(r"[가-힣]{2}", t):      # 한글 낱말이 있으면 토큰은 남기고 앞뒤 잡음만 뗀다(_strip_token_edges)
        return False
    hanja = len(_HANJA_RE.findall(t))
    bad = sum(1 for c in t if not _OK_CHAR_RE.match(c)) - hanja
    good = re.search(r"[A-Za-z]{3}|\d|[가-힣]", t)
    return (not good and bool(bad or hanja)) or (bad + hanja >= 2 and bad + hanja >= len(t) / 2)


def _strip_token_edges(t: str, keep_guil: bool = False) -> str:
    """'一오스틴' → '오스틴', '臣麗因주기도문은' → '주기도문은' (한글 어절 앞뒤에 붙은 한자·기호).
    keep_guil: 줄에 짝 맞는 '«…»'가 있으면 '«»'는 떼지 않는다."""
    if not re.search(r"[가-힣]{2}", t) or re.search(r"\([一-鿿]+\)", t):
        return t
    cls = "[一-鿿㐀-䶿々^■•*~`]" if keep_guil else "[一-鿿㐀-䶿々^■•*«»~`]"
    return re.sub(rf"^{cls}+|{cls}+$", "", t)


def _clean_line(line: str) -> str:
    """잡음 줄이면 '', 본문 줄이면 섞인 잡음 토큰과 양끝 잡음을 뗀 줄."""
    keep_guil = bool(_PAIRED_GUIL_RE.search(line))
    # 잡음 토큰과, 낱말 사이에 홀로 떨어진 따옴표 토큰("가, ' 무시당한다."의 "'")을 뗀다
    line = " ".join(_strip_token_edges(t, keep_guil) for t in line.split()
                    if not _is_noise_token(t) and not re.fullmatch(r"[‘’'\"“”]", t))
    # 저자 출처 줄('-J. H. 에번스', '一찰스 스탠리(1932-)')은 이니셜·연도가 많아도 본문
    if re.match(r"^[-一—–_]\s?(?:[A-Z]\.\s?)*[가-힣A-Z][가-힣A-Za-z.]*(?:\s[가-힣A-Za-z.]+){0,3}\s*(?:\(|,|$)", line):
        return line
    hangul = len(re.findall(r"[가-힣]", line))
    if hangul >= _KEEP_HANGUL:
        line = _trim_junk_edges(line)
        hangul = len(re.findall(r"[가-힣]", line))
        if hangul < 10 and _low_word_quality(line):
            return _salvage(line)
        return line
    if _low_word_quality(line):
        return _salvage(line)
    # 한문 인용 줄('學而時習之 不亦說乎')은 본문 — 한자 4자↑ 연속이고 한자·공백·문장부호만 있을 때
    # (영문·숫자가 섞인 '秘密永近Wteil' 같은 그림 속 글자 오인식은 제외)
    if re.search(r"[一-鿿]{4,}", line) and all(
            _HANJA_RE.match(c) or c.isspace() or c in ".,!?;:·()「」『』“”\"'" for c in line):
        return line
    # 한글이 적은 짧은 줄: 한자가 섞였거나 한글 낱말·영단어(모음 포함 3자↑)가 없으면 잡음
    if not re.fullmatch(r"[\d\-.,:/()| ]+", line.strip()) and (
            (_HANJA_RE.search(line) and not re.search(r"[一-鿿]{4,}", line)
             and not _CONTENT_TOKEN_RE.search(line))   # 한문 인용 줄·한자 병기·'甲이' 보호
            # 숫자+한글('5월 9일 - 10일')·띄어 쓴 표 머리('합 계', '의 견')는 본문
            or not (re.search(r"[가-힣]{2}", line) or _NUM_UNIT_RE.search(line)
                    or _SPACED_HEADER_RE.match(line.strip())
                    or re.search(r"(?=[A-Za-z]{3})[A-Za-z]*[aeiouyAEIOUY][A-Za-z]*", line))):
        return ""
    return "" if is_garbage_line(line) else line


def _join_latin_word_lines(lines: list[str]) -> list[str]:
    """한 단어씩 끊긴 영어 줄('Our' / 'success' / 'has' …)을 앞 줄에 잇는다."""
    out: list[str] = []
    for l in lines:
        s = l.strip()
        if (out and re.fullmatch(r"[A-Za-z’'.,;:!?-]{1,20}", s)
                and re.search(r"[A-Za-z’'.,;:-]$", out[-1].rstrip())):
            out[-1] = out[-1].rstrip() + " " + s
        else:
            out.append(l)
    return out


# 한글 사이 쉼표 뒤 공백 — 전각 쉼표('，')는 OCR 이 뒤 공백 없이 읽어, 반각으로 바꾸면
# '어느 날,첫사랑의'처럼 붙는다(명언집 5권 2,334곳 실측). 숫자 '1,000'·영문은 건드리지 않는다
_COMMA_SPACE_RE = re.compile(r"(?<=[가-힣]),(?=[가-힣“‘\"'(])")


def clean_page(text: str) -> str:
    """쪽 하나의 OCR 잡음을 걷어낸다(순서 중요: 부호 정리 → 번호 복원 → 잡음 줄 판정)."""
    text = text.translate(_FULLWIDTH)
    text = _COMMA_SPACE_RE.sub(", ", text)
    text = _RULE_RE.sub(" ", text)
    text = _JAMO_TOKEN_RE.sub(" ", text)
    text = _JAMO_CHAR_RE.sub("", text)
    text = _SYMBOL_TOKEN_RE.sub(" ", text)
    text = _SYMBOL_LEAD_RE.sub("", text)
    text = _fix_numbers(text)
    lines = [re.sub(r"[ \t]{2,}", " ", l).strip() for l in text.splitlines()]
    lines = [c for c in (_clean_line(l) for l in lines) if c]
    return "\n".join(_join_word_per_line(_join_latin_word_lines(lines)))


def _join_word_per_line(lines: list[str]) -> list[str]:
    """큰 글씨 제목이 한 낱말씩 줄바꿈된 것('자다가 / 일어날 / 정도로 / 두근거리는 …')을 한 줄로 —
    한 낱말(한글 8자 이하)짜리 줄이 3줄 이상 이어질 때만(시·목록의 짧은 줄 보호)."""
    one = lambda l: bool(re.fullmatch(r"[“\"'‘(]?[가-힣]{1,8}[.,!?”\"'’)…]*", l.strip()))
    out, run = [], []
    for l in lines + [None]:
        if l is not None and one(l):
            run.append(l.strip())
            continue
        out += [" ".join(run)] if len(run) >= 3 else run
        run = []
        if l is not None:
            out.append(l)
    return out


# ── 문서 단위: 깨진 러닝헤더 ─────────────────────────────────────────────────────
_GRAM = 4
_HEADER_MIN_SPAN = 8       # 머리글로 지울 구간의 최소 글자(한글·영문) 수('천재■의생각아포리즘' 9자)
_HEADER_COVER = 0.5        # 구간 4글자 조각 중 머리글 조각 비율
DEBUG_LOG: list | None = None   # 점검용 — 리스트를 넣으면 (원래 줄, 지운 뒤 줄)을 모은다


def _skel(s: str) -> str:
    return re.sub(r"[^가-힣A-Za-z]", "", s).lower()


def _grams(sk: str) -> set[str]:
    return {sk[i:i + _GRAM] for i in range(len(sk) - _GRAM + 1)}


def _header_grams(pages: list[str]) -> set[str]:
    """쪽 가장자리(첫·끝 2줄)에 여러 쪽 반복되는 4글자 조각 중, 본문 쪽에는 드문 것."""
    edge_df, all_df = Counter(), Counter()
    for p in pages:
        lines = [l for l in p.splitlines() if l.strip()]
        # 가장자리 줄의 양끝 30자만 — 머리글은 본문 줄 앞뒤에 붙어 읽힌다. 줄 전체를 보면
        # 쪽 끝에 자주 오는 영어 명언 줄의 흔한 조각('that'·'ally')까지 학습된다
        edge = [s for l in lines[:2] + lines[-2:] for s in (l.strip()[:30], l.strip()[-30:])]
        edge_df.update(set().union(*(_grams(_skel(l)) for l in edge)) if edge else set())
        all_df.update(_grams(_skel(p)))
    need = max(5, int(len(pages) * 0.03))
    return {g for g, n in edge_df.items() if n >= need and n >= 0.6 * all_df[g]}


# 쪽번호 토큰 — 깨져 읽힌 꼴 포함('•)88', '8(i', '|]8', '9()(5', '(J2', ']$)($')
_PAGE_NO_TOKEN_RE = re.compile(r"^(?=.*[\d$])[•·|I()\[\]$]*[\dIlOoSiJ()\[\]$?]{1,5}[•·|)\]]*$|^\d{1,4}$")


def _strip_header_spans(line: str, hg: set[str]) -> str:
    """머리글 조각에 글자 대부분이 덮인 어절이 이어진 구간(합계 _HEADER_MIN_SPAN자↑)만 지운다.
    구간 판정을 어절 단위로 해야 머리글 앞뒤의 본문 어절이 함께 지워지지 않는다."""
    toks = line.split()
    sks = [_skel(t) for t in toks]
    line_sk = "".join(sks)
    covered = [False] * len(line_sk)
    for p in range(len(line_sk) - _GRAM + 1):
        if line_sk[p:p + _GRAM] in hg:
            for q in range(p, p + _GRAM):
                covered[q] = True
    hg3 = {g[p:p + 3] for g in hg for p in range(2) if re.fullmatch(r"[가-힣]{3}", g[p:p + 3])}
    hdr, pos = [], 0
    for sk in sks:
        c = sum(covered[pos:pos + len(sk)])
        hdr.append(len(sk) >= 2 and c / len(sk) >= 0.6)
        pos += len(sk)
    drop: set[int] = set()
    i = 0
    while i < len(toks):
        if not hdr[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(toks) and (hdr[j + 1] or (not sks[j + 1] and j + 2 < len(toks) and hdr[j + 2])):
            j += 1
        if sum(len(sks[k]) for k in range(i, j + 1) if hdr[k]) >= _HEADER_MIN_SPAN:
            # 바로 앞 어절이 더 심하게 깨진 머리글 조각('실리콘IB리')이면 함께 — 머리글 3글자 조각을 품을 때만
            if i > 0 and len(sks[i - 1]) >= 3 and any(sks[i - 1][p:p + 3] in hg3
                                                       for p in range(len(sks[i - 1]) - 2)):
                i -= 1
            span = set(range(i, j + 1))
            # 머리글 바로 앞뒤의 쪽번호 토큰 하나씩('164 실리콘…', '… sellers •)88')
            k = i - 1
            if k >= 0 and toks[k] in ("I", "|", "•", ":", "'", "’", '"'):   # '146 I 1000 WISE…'
                span.add(k)
                k -= 1
            if k >= 0 and _PAGE_NO_TOKEN_RE.match(toks[k]):
                span.add(k)
            # 영문 머리글 끝 단어가 깨져 조각이 안 맞는 경우('… MASTERPIECE RLMS')
            if j + 1 < len(toks) and re.fullmatch(r"[A-Z]{2,6}", toks[j + 1]) \
                    and re.fullmatch(r"[A-Za-z]+", toks[j]):
                j += 1
                span.add(j)
            # 뒤따르는 쪽번호 하나 — 단 '407 I 제목'처럼 항목 번호+구분자이면 남긴다
            if j + 1 < len(toks) and _PAGE_NO_TOKEN_RE.match(toks[j + 1]) \
                    and not (j + 2 < len(toks) and toks[j + 2] in ("I", "|")):
                span.add(j + 1)
            drop |= span
        i = j + 1
    return " ".join(t for k, t in enumerate(toks) if k not in drop) if drop else line


# 'PART 3 실라콘뱅리 혁신가들의 통학 331' — 장 머리글+쪽번호가 본문 줄 가운데에 끼어든 것.
# 쪽번호(1~3자리)가 뒤따를 때만 머리글로 본다. 번호가 둘이면('명대사 51 115 언제') 앞 것만 쪽번호.
# 장·부 머리글 표지 — 영문 대문자 'PART'·'CHAPTER'(OCR 깨짐 포함). 한글 '제N장'은 본문 문장
# ('제2장에서 살펴본 3가지')과 구분되지 않아 쓰지 않는다
_PART = r"(?:P ?A ?R ?T|PA.T|P.RT|part|CHAPTER|Chapter)"
# 머리글 구간 안에 다른 장 표지가 오면 머리글이 아니다 — 2단 차례 머리 'Chapter 2 Chapter 3 …'의
# '3'을 쪽번호로 읽어 지웠다(하루명화하루명언 실측)
_PART_HEADER_RE = re.compile(
    rf"\b{_PART}\s?\d{{1,2}}\b(?:(?!{_PART})[^\n]){{0,40}}?(?<!\S)[•·|I]?\s*\d{{1,3}}(?=\s|$)")
_PART_LINE_RE = re.compile(rf"^{_PART}\s?(\d{{1,2}})\b.{{0,40}}$")


def _strip_part_headers(pages: list[str]) -> tuple[list[str], int]:
    seen: set[str] = set()
    out, n = [], 0
    for p in pages:
        if _is_toc_page(p) or _is_toc_part_page(p):   # 차례의 '장 제목 + 쪽번호'는 머리글이 아니라 차례 항목
            out.append(p)
            continue
        new = []
        for l in p.splitlines():
            s = re.sub(r" {2,}", " ", _PART_HEADER_RE.sub(" ", l)).strip()
            if s == l.strip():
                s = l
            m = _PART_LINE_RE.match(s.strip())
            if m:                   # 번호 없이 'PART N 장제목' 만 있는 줄 — 첫 등장(장 표제)만 남긴다
                if m.group(1) in seen:
                    s = ""
                seen.add(m.group(1))
            if s != l:
                n += 1
            if s.strip() or not l.strip():
                new.append(s)
        out.append("\n".join(new))
    return out, n


def strip_fuzzy_headers(pages: list[str]) -> tuple[list[str], int]:
    """쪽마다 다르게 깨져 읽힌 머리글 구간을 지운다. (쪽들, 지운 줄 수)"""
    pages, n_part = _strip_part_headers(pages)
    hg = _header_grams(pages)
    if not hg:
        return pages, n_part
    out, n = [], n_part
    for p in pages:
        new = []
        for l in p.splitlines():
            s = _strip_header_spans(l, hg)
            if s != l and is_garbage_line(s):       # 머리글을 떼고 남은 자투리('1 期')
                s = ""
            if s != l and DEBUG_LOG is not None:
                DEBUG_LOG.append((l, s))
            if s != l:
                n += 1
            if s.strip() or not l.strip():
                new.append(s)
        out.append("\n".join(new))
    return out, n


# ── 문서 단위: 앞뒤 부속물(규칙 판정) ─────────────────────────────────────────────
# Gemini 문단 분류(drop_front_paragraphs)는 비용 우선에서 쓰지 않는다. 실측(명언집 5권, 부속물 삭제
# 켜고 처리): 판권(마지막 쪽)과 '차례' 제목 쪽 1장만 지워지고 표지·저자 소개·다른 책 광고·저작권 쪽·
# 차례 이어지는 쪽이 그대로 남았다. 모양이 뚜렷한 것만 규칙으로 고른다 — 머리말·프롤로그는 본문.
_FRONT_MAX_PAGES = 40
_PRICE_AD_RE = re.compile(r"\d[\d,.]{3,6}\s*원")
# 도서 광고 문맥: '지음·옮김', '| 175면 |'·'384쪽' 같은 쪽수, '김남진 역 | 15,000원'·'이상혁 저 I' 처럼
# 구분자가 뒤따르는 '저'·'역'. 대명사 '저'('저 역시')와 헷갈리지 않게 '저'·'역' 단독은 쓰지 않는다
_PRICE_CTX_RE = re.compile(r"지음|옮김|저자|역자|\d\s*면\s*[|｜I]|\d{2,4}\s*쪽|(?<![가-힣])[저역]\s*[|｜I](?![A-Za-z])")
_COPYRIGHT_RE = re.compile(r"Copyright|©|저작권법|무단\s*(?:전재|복제)|ISBN|All rights reserved|출판권", re.I)
_BIO_WORDS = ("저자", "지은이", "옮긴이", "대표이사", "큐레이터", "졸업", "학위", "저서", "출간", "강연",
              "페이스북", "블로그", "http", "인문학자", "facebook")
_BODY_START_RE = re.compile(r"^(?:prologue|프롤로그|머리말|서문|들어가며|들어가는 글|여는 글)", re.I)


_SENTENCE_END_RE = re.compile(r"[다요죠][.!?]?(?:\s|$)")
_BOOK_MIN_PAGES = 30       # 위치(앞부분)에 기대는 판정(표지·저자 소개·차례)은 책 분량 문서에만


def _is_toc_page(p: str) -> bool:
    """차례 쪽 꼴: 짧은 줄이 많고 상당수가 쪽번호(숫자)로 끝나거나 숫자뿐이며, 문장이 거의 없거나
    쪽 분량이 적은 쪽. 2단 편집 기사(짧은 줄 + 'www.dongabiz.com 127' 같은 숫자 끝 줄)는 줄 모양이
    차례와 같아 본문 쪽을 지웠다(DBR 매몰비용 실측) — 기사 쪽은 1,900자↑·문장 16개↑, 차례 쪽은
    400~1,100자(명언집 차례는 장 제목이 '…하라.' 문장이라 문장 수만으로는 못 가른다)."""
    lines = [l.strip() for l in p.splitlines() if l.strip()]
    if len(lines) < 12 or (len(_SENTENCE_END_RE.findall(p)) > 3 and len(p) > 1300):
        return False
    avg = sum(len(l) for l in lines) / len(lines)
    numeric = sum(1 for l in lines if re.search(r"(?:^|\s|[가-힣.])\d{1,3}$", l))
    return avg <= 30 and numeric / len(lines) >= 0.2


# 차례 덩어리의 첫 쪽 표지어 — 제목('차례'·'CONTENTS') 또는 차례 첫 항목('prologue 4'·'머리말')
_TOC_START_RE = re.compile(r"(?i)차\s?례|목\s?차|contents|prolog|프롤로그|머리말|서\s?문|들어가며")


def _is_toc_part_page(p: str) -> bool:
    """'Part 10 / 장 제목 / #태그 #태그 …' 꼴 차례 쪽(장별 차례)."""
    return bool(re.search(r"(?i)\bpart\s?\d", p)) and len(re.findall(r"#[가-힣A-Za-z]", p)) >= 3


def drop_book_matter_rules(pages: list[str], notes: list[str]) -> list[str]:
    """비용 우선(규칙) 경로의 앞뒤 부속물 제거. 지운 쪽은 빈 자리로 두고 notes 에 보고."""
    out = list(pages)
    n = len(out)
    win = min(n, _FRONT_MAX_PAGES, max(10, n // 6))
    found: dict[str, list[int]] = {}

    def drop(i: int, kind: str) -> None:
        if out[i].strip():
            out[i] = ""
            found.setdefault(kind, []).append(i + 1)

    # 본문 시작 경계: 머리말·프롤로그 제목 쪽과 첫 차례 쪽 중 앞선 것. 둘 다 없으면 600자 넘는 첫 쪽
    first = lambda pred: next((i for i in range(win) if pred(out[i])), None)
    head_lines = lambda p: [l.strip() for l in p.splitlines() if l.strip()][:3]
    marks = [x for x in (first(lambda p: any(_BODY_START_RE.match(l) for l in head_lines(p))),
                         first(lambda p: _is_toc_page(p) and bool(_TOC_START_RE.search(p)))) if x is not None]
    bound = min(marks) if marks else (first(lambda p: len(p.strip()) >= 600) or 0)
    # 문장으로 된 쪽(머리말 등)은 표지·소개로 보지 않는다 — '…다.' 꼴 종결이 둘 이상이면 본문
    sentences = lambda p: len(_SENTENCE_END_RE.findall(p))
    is_book = n >= _BOOK_MIN_PAGES   # 짧은 기사·보고서는 위치 기반 판정 없이 저작권·광고만

    for i in range(win):
        p = out[i]
        if not p.strip():
            continue
        if _PRICE_AD_RE.search(p) and _PRICE_CTX_RE.search(p):
            drop(i, "다른 책 광고")
        elif len(_COPYRIGHT_RE.findall(p)) >= 2:
            drop(i, "저작권·판권")
        elif is_book and i < bound and len(p) < 1500 and (
                re.search(r"(?m)^\s*(?:저자|지은이|옮긴이|역자)\s", p)
                or sum(w in p for w in _BIO_WORDS) >= 3):
            drop(i, "저자 소개")
        elif is_book and i < bound and len(p.strip()) < 500 and sentences(p) < 2:
            drop(i, "표지·제목")
    # 차례: 앞쪽의 첫 차례 덩어리만 — 차례 꼴(또는 장별 차례 꼴) 쪽이 이어지는 동안.
    # 본문에도 '번호 / 짧은 제목' 줄이 많은 쪽이 있어(기독교명언) 덩어리 하나에서 멈춘다
    # 첫 쪽은 차례 꼴이면서 차례 표지어가 있어야 한다 — 숫자 목록 본문(TRIZ '1~40 발명 원리'·그림·표가 든
    # 기사 쪽 실측)은 모양이 차례와 같아 표지어 없이 시작을 인정하면 본문을 지운다
    # (표지어는 차례 첫 쪽이 짧아 차례 꼴에 못 미친 경우를 위해 바로 앞 쪽까지 본다 — 악당의명언 8·9쪽)
    start = next((i for i in range(win) if _is_toc_page(out[i]) and (
        _TOC_START_RE.search(out[i]) or (i > 0 and _TOC_START_RE.search(out[i - 1])))), None) \
        if is_book else None
    if start is not None:
        j = start
        while j < win and (not out[j].strip() or _is_toc_page(out[j]) or _is_toc_part_page(out[j])):
            drop(j, "차례")
            j += 1
    # 뒤쪽 다른 책 광고
    for i in range(max(win, n - 5), n):
        if _PRICE_AD_RE.search(out[i]) and _PRICE_CTX_RE.search(out[i]):
            drop(i, "다른 책 광고")
    for kind, idxs in found.items():
        notes.append(f"앞부속·뒤부속 자동 제거 — {kind} {', '.join(map(str, idxs))}번째 쪽"
                     " (정제 전 원문 사본에서 확인 가능)")
    return out


# ── 쪽 단위: 출처 표기 명언집의 항목 그룹 경계 ─────────────────────────────────────
# 실측(기독교명언 43~50): OCR 이 항목 번호 44·47·48·49 를 아예 읽지 못했다. 원문은 줄 구조
# '명언 줄 → 一저자(생몰년), 직함 → 성경의 진리 상자'가 살아 있으나, 줄 잇기가 저자 줄·상자·다음 명언을
# 한 줄로 이어 그룹이 사라졌다. 저자 출처 줄 바로 앞 명언 줄(앞의 주제어·번호 포함)에서 단락을 연다.
_ATTR_LINE_RE = re.compile(r"^\s*[-一—–~_]?\s?[가-힣A-Za-z][가-힣A-Za-z.·' ]{1,30}\(\s?\d{3,4}\s?[-—–]")
_ATTR_BOOK_MIN = 30        # 출처 줄이 이만큼 있는 책에서만(일반 문서의 '홍길동(1950-2010)' 줄 보호)
# 그룹 경계 표지 줄 — 빈 줄은 뒤의 '끊긴 문장 잇기'가 건너뛰어 다시 붙인다(앞 그룹 끝 성경 상자가
# OCR 에서 '…(창'처럼 잘려 미완결 문장으로 보임). 줄 잇기가 끝난 뒤 finish_groups 가 빈 줄로 바꾼다
GROUP_MARK = "⟨GROUP⟩"


def group_attributed_quotes(pages: list[str]) -> tuple[list[str], int]:
    """출처 표기 명언집: 저자 출처 줄 앞의 명언 줄(앞에 붙은 주제어·번호 줄 포함) 앞에 빈 줄을 넣어
    항목 그룹 단락을 연다 — 번호를 못 읽은 항목도 그룹으로 나뉜다. (쪽들, 연 단락 수)"""
    if sum(1 for p in pages for l in p.splitlines() if _ATTR_LINE_RE.match(l)) < _ATTR_BOOK_MIN:
        return pages, 0
    out, n = [], 0
    for p in pages:
        lines = p.split("\n")
        res: list[str] = []
        for i, l in enumerate(lines):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if l.strip() and _ATTR_LINE_RE.match(nxt) and not _ATTR_LINE_RE.match(l):
                j = len(res)
                # 바로 앞의 항목 번호 줄·짧은 주제어 줄('경건함', '고난')은 이 그룹의 머리
                while j > 0 and res[j - 1].strip() and (
                        re.fullmatch(r"\s*\d{1,4}\s*", res[j - 1])
                        or (len(res[j - 1].strip()) <= 12 and not _SENT_END_RE.search(res[j - 1]))):
                    j -= 1
                if j > 0 and res[j - 1].strip():
                    res.insert(j, GROUP_MARK)
                    n += 1
            res.append(l)
        out.append("\n".join(res))
    return out, n


def finish_groups(text: str) -> str:
    """그룹 경계 표지 줄을 단락 경계(빈 줄)로 바꾼다 — 줄 잇기 단계가 모두 끝난 뒤에 부른다."""
    text = re.sub(rf"[ \t]*{re.escape(GROUP_MARK)}[ \t]*", "\n\n", text)
    return re.sub(r"\n{3,}", "\n\n", text)


# ── 줄 단위: 출처 인용 괄호 짝 복원 ─────────────────────────────────────────────
# 실측(시네마 133줄·베스트셀러 249줄): OCR 이 괄호 모양을 잘못 읽어 짝이 깨진다 —
#   '〈렛미인(…)>(2008)'(닫는 꺾쇠 → '>'), '〈용서받지 못한 자X2005)'('〉(' → 'X'),
#   '「소년이온다j'·'『길매기의 꿈J'(닫는 낫표 → j/J), 'r그리스도를 본받아」'(여는 낫표 → r),
#   '「파리대왕』'(여닫는 종류 불일치). 같은 줄 안에서 짝이 분명할 때만 고친다.
_CLOSER = {"「": "」", "『": "』", "〈": "〉", "《": "》"}
_YEAR = r"[\dOolI]{4}"
_ANGLE_RE = re.compile(rf"[〈<](?=[가-힣])([^〈〉<>\n]{{1,80}}?)(?:[>〉》]|X(?={_YEAR}\)))")
_YEAR_AFTER_RE = re.compile(rf"〉\(?({_YEAR})\)")
_R_OPENER_RE = re.compile(r"(?<![A-Za-z0-9])r(?=[가-힣][^\n「『」』]{0,40}?(?:[」』]|[jJ](?![A-Za-z])))")
_J_CLOSER_RE = re.compile(r"([「『])([^「『」』\n]*?[가-힣][^「『」』\n]*?)[jJ](?![A-Za-z])")
_MISMATCH_RE = re.compile(r"([「『])([^「『」』\n]*?)([」』])")


def repair_quote_brackets(text: str) -> tuple[str, int]:
    """출처 인용 괄호(〈〉「」『』)의 OCR 오인식을 같은 줄 안의 짝으로 복원한다. (텍스트, 고친 곳 수)"""
    n = 0

    def angle(m: re.Match) -> str:
        nonlocal n
        pair = m.group(0)[0] + m.group(0)[-1]
        if pair in ("〈〉", "<>"):
            return m.group(0)               # 이미 짝이 맞음 — 디지털 문서의 '<그림 3>'·'<군주론>'은 표기 그대로
        n += 1
        closer = "〉(" if m.group(0).endswith("X") else "〉"
        return f"〈{m.group(1)}{closer}"

    def year(m: re.Match) -> str:
        return "〉(" + m.group(1).translate(str.maketrans("OolI", "0011")) + ")"

    def j_close(m: re.Match) -> str:
        nonlocal n
        n += 1
        return m.group(1) + m.group(2) + _CLOSER[m.group(1)]

    def mismatch(m: re.Match) -> str:
        nonlocal n
        if _CLOSER[m.group(1)] == m.group(3):
            return m.group(0)
        n += 1
        return m.group(1) + m.group(2) + _CLOSER[m.group(1)]

    out = []
    for line in text.split("\n"):
        line, k = _R_OPENER_RE.subn("「", line)
        n += k
        line = _J_CLOSER_RE.sub(j_close, line)
        line = _MISMATCH_RE.sub(mismatch, line)
        line = _ANGLE_RE.sub(angle, line)
        line = _YEAR_AFTER_RE.sub(year, line)
        out.append(line)
    return "\n".join(out), n


# ── 문서 단위: 한글·영어 문장 경계 줄바꿈 ─────────────────────────────────────────
# 실측(실리콘밸리 정제본 1,919줄 중 402줄): 한글 명언 뒤에 영어 원문이 한 줄로 붙어 읽기 어렵다
# ('0264 모든 사람에게 … 주어라 Give everyone the power to share …'). 줄 잇기(join_broken_lines)가
# 문장부호 없는 한글 줄과 다음 영어 줄을 이어 붙인 것. 문장 단위 언어 전환에서만 줄을 바꾼다.
_KO_EN_GAP_RE = re.compile(r"(?<=[가-힣.!?”\"’'\)\],\-])[ \t]+(?=[“\"'‘]?[A-Z])")
_EN_KO_GAP_RE = re.compile(r"(?<=[A-Za-z][.!?])[”\"’']?[ \t]+(?=[“\"'‘]?[가-힣]{2})")
# 영어 인용 뒤에 바로 붙는 조사·인용어 — 본문 속 인용('“Stay hungry.”라고 말했다')은 끊지 않는다
_QUOTE_TAIL_RE = re.compile(
    r"^[”\"’']?\s*(?:라고|이라고|라는|이라는|란|이란|라며|이라며|하고|는|은|을|를|의|에|에서|와|과|로|으로|처럼|같은|이다|다)(?![가-힣])"
    r"|^[”\"’']?(?:라고|이라고|라는|이라는|라며|이라며)")
_MIN_EN_WORDS = 4


def _en_words(s: str) -> int:
    return len(re.findall(r"[A-Za-z]{2,}", s))


def _split_language_line(line: str) -> list[str]:
    # 한글 → 영어: 앞 한글 8자↑, 뒤 영어 문장(단어 4개↑)이 줄 끝까지 가거나 문장부호로 끝난 뒤 한글이
    # 이어질 때 — 한글 문장 속 영어 기관명·표 머리('… New Jersey Institute of Technology 교수를 거쳐')는 끊지 않는다
    for m in _KO_EN_GAP_RE.finditer(line):
        head, tail = line[:m.start()], line[m.end():]
        if len(re.findall(r"[가-힣]", head)) < 8:
            continue
        en = re.split(r"(?=[가-힣])", tail, maxsplit=1)[0]
        after = tail[len(en):]
        if _en_words(en) < _MIN_EN_WORDS or (after and (
                _QUOTE_TAIL_RE.match(after) or not re.search(r"[.!?][”\"’')]?\s*$", en))):
            continue
        return [head.rstrip()] + _split_language_line(tail)
    # 영어 → 한글: 앞 영어 문장(문장부호로 끝남, 단어 4개↑) 뒤에 한글 문장(6자↑)이 시작될 때
    for m in _EN_KO_GAP_RE.finditer(line):
        head, tail = line[:m.start()], line[m.end():]
        en_head = re.split(r"[가-힣]", head)[-1]
        if _en_words(en_head) < _MIN_EN_WORDS or len(re.findall(r"[가-힣]", tail)) < 6 \
                or _QUOTE_TAIL_RE.match(tail):
            continue
        quote = line[m.start():m.end()].strip()     # 닫는 따옴표는 영어 쪽에 남긴다
        return [(head + quote).rstrip()] + _split_language_line(tail)
    return [line]


def split_language_lines(text: str) -> tuple[str, int]:
    """한 줄에 붙은 한글 문장과 영어 문장을 줄바꿈으로 가른다(단락은 유지). (텍스트, 나눈 곳 수)"""
    out: list[str] = []
    for line in text.split("\n"):
        out.extend(_split_language_line(line))
    joined = "\n".join(out)
    return joined, joined.count("\n") - text.count("\n")


# ── 문서 단위: 번호 항목 단락 ──────────────────────────────────────────────────
# 항목 번호 후보: 단독 숫자 토큰(뒤에 단위·구두점이 붙지 않음)
# (단위는 숫자에 바로 붙은 것만 본다 — '0001 명언' 의 '명'을 단위로 오인하지 않게)
_ENTRY_RE = re.compile(r"(?<![\w(\-:.,/])(\d{1,4})(?![\w%)\-:.,/~])")
_ENTRY_GAP = 4             # OCR 이 놓친 번호를 건너뛰어도 되는 폭
_ENTRY_LOOKBACK = 400      # DP 에서 되돌아보는 후보 수
_ENTRY_MIN_CHAIN = 30      # 이보다 짧은 연쇄는 번호 항목 책이 아니다(본문 숫자 우연 일치)


def _entry_chain(text: str) -> list[re.Match]:
    cands = [m for m in _ENTRY_RE.finditer(text)]
    vals = [int(m.group(1)) for m in cands]
    best = [1] * len(cands)
    prev = [-1] * len(cands)
    for j in range(len(cands)):
        for i in range(max(0, j - _ENTRY_LOOKBACK), j):
            if 1 <= vals[j] - vals[i] <= _ENTRY_GAP and best[i] + 1 > best[j]:
                best[j], prev[j] = best[i] + 1, i
    if not cands:
        return []
    k = max(range(len(cands)), key=lambda x: best[x])
    chain = []
    while k != -1:
        chain.append(cands[k])
        k = prev[k]
    return chain[::-1]


# ── 문서 단위: 몰려 읽힌 번호를 본문에 되돌리기 ──────────────────────────────────
# 실측(시네마명언 14곳·실리콘밸리 68곳): 번호가 본문 왼쪽 여백에 따로 인쇄된 책은 RapidOCR 이
# 한 쪽의 번호들('014' '015')을 먼저 몰아 읽고 본문을 뒤에 읽는다 → 단락 복원 후 번호만 있는
# 빈 항목(실리콘밸리 173개)이 생기고 본문은 번호 없이 앞 항목에 붙었다. 내용 손실이 아니라 순서 어긋남.
# 번호만 있는 줄. 쪽 경계에서 앞 쪽 쪽번호가 붙어 '19 014' 가 된 줄도 포함(쪽번호는 버린다)
_NUM_LINE_RE = re.compile(r"^\s*(?:\d{1,3}\s+)?(\d{1,4})\s*$")
_SENT_END_RE = re.compile(r"(?:[.!?”\"’')\]]|[다요죠까니])\s*$")
_BOUND_HEAD_RE = re.compile(
    r"^(?:며|으며|니|으니|지만|면서|도록|거나|는데|은데|으면|는|은|을|를|에|의|에서|에게|으로|로"
    r"|었다|았다|였다|했다|한다|된다|합니다|입니다|습니다|니다|리는|하는|이다)(?![가-힣])")


def _is_unit_start(line: str, nxt: str = "") -> bool:
    """본문 덩어리의 첫 줄 — 한글로 시작. 12자 이하 짧은 줄은 소제목('사라지지 않는다')일 수 있어
    바로 뒤에 영어 번역 줄이 올 때만 덩어리로 본다."""
    s = line.strip()
    if not re.match(r"[“\"'‘(]?[가-힣]", s):
        return False
    return len(s) > 12 or bool(re.match(r"\s*[A-Z][a-z]", nxt))


def redistribute_number_stacks(text: str) -> tuple[str, int]:
    """'014 / 015 / 본문A / 본문B' 처럼 몰려 읽힌 연속 번호를 뒤따르는 본문 덩어리 머리에 차례로 붙인다.
    번호 바로 뒤의 앞 항목 꼬리(영어 번역 줄·끊긴 문장 잇는 줄)는 건너뛴다. (텍스트, 옮긴 번호 수)
    번호 항목 책(is_numbered_book)에만 적용할 것 — 일반 문서의 표·목록 숫자 줄을 본문 머리에 붙이지 않게."""
    raw = text.split("\n")
    lines: list[str] = []
    for k, l in enumerate(raw):
        # 쪽 경계에서 몰림의 앞 번호들이 앞 문장 끝에 붙은 경우('…날인 0027' / '0028',
        # '…한다 0032 0033' / '0034') — 연속 번호면 떼어 낸다
        m_tail = re.match(r"^(.*?[가-힣A-Za-z.,!?”\"’')][^\d]*?)((?:\s+\d{2,4})+)\s*$", l)
        if m_tail:
            tail = [int(x) for x in m_tail.group(2).split()]
            nxt = _NUM_LINE_RE.match(raw[k + 1]) if k + 1 < len(raw) else None
            seq = all(b - a == 1 for a, b in zip(tail, tail[1:]))
            # 다음 줄 번호와 이어지거나, 3자리↑ 연속 번호 둘 이상('0032 0033')일 때만 — '시즌 1 2' 같은 본문 제외
            if seq and ((nxt and int(nxt.group(1)) == tail[-1] + 1)
                        or (len(tail) >= 2 and all(len(x) >= 3 for x in m_tail.group(2).split()))):
                lines += [m_tail.group(1).rstrip()] + m_tail.group(2).split()
                continue
        # 영어 번역 줄 끝에 다음 항목의 한글이 붙은 줄('…define it 행동가들은 …') — 한글 앞에서 나눈다
        m_mix = re.match(r"^([A-Za-z‘'\"].*?[A-Za-z.,!?’'\"])\s+([“\"'‘]?[가-힣]{2,}.*)$", l)
        if m_mix and len(re.findall(r"[가-힣]", m_mix.group(2))) >= 6 \
                and len(re.findall(r"[A-Za-z]{2,}", m_mix.group(1))) >= 3:   # 영어 쪽이 문장일 때만('Boss 매출이…' 제외)
            lines += [m_mix.group(1), m_mix.group(2)]
            continue
        lines.append(l)
    out: list[str] = []
    moved = 0
    i = 0
    prev_num = None
    while i < len(lines):
        m = _NUM_LINE_RE.match(lines[i])
        j = i
        while j < len(lines) and _NUM_LINE_RE.match(lines[j]):
            j += 1
        vals = [int(_NUM_LINE_RE.match(lines[k]).group(1)) for k in range(i, j)]
        if not m or len(vals) < 2 or any(b - a != 1 for a, b in zip(vals, vals[1:])):
            lm = re.match(r"^\s*(\d{1,4})(?=\s|$)", lines[i])
            if lm:
                prev_num = int(lm.group(1))
            out.append(lines[i])
            i += 1
            continue
        # 번호 몰림 [i, j) — 뒤따르는 덩어리 범위: 다음 번호 줄 전까지. 빈 줄(쪽 경계)은 덩어리가
        # 아직 모자랄 때만 넘는다(최대 40줄) — 본문이 다음 쪽으로 넘어가 읽힌 경우
        end = j
        found = 0
        while end < len(lines) and end - j < 40 and not _NUM_LINE_RE.match(lines[end]) \
                and not re.match(r"^\s*\d{2,4}\s+\S", lines[end]):
            if not lines[end].strip() and found >= len(vals) + 1:
                break
            if _is_unit_start(lines[end], lines[end + 1] if end + 1 < len(lines) else ""):
                found += 1
            end += 1
        while end > j and not lines[end - 1].strip():
            end -= 1
        starts = [k for k in range(j, end)
                  if _is_unit_start(lines[k], lines[k + 1] if k + 1 < end else "")]
        # 앞 항목 꼬리: 첫 한글 줄이 조사·어미로 시작하거나, 번호 몰림 앞 본문이 문장 중간에서 끊겼으면.
        # 앞 본문 = 번호 몰림 바로 앞 줄(영어 번역 줄이면 그 앞 한 줄). 'PART 1 … 명대사' 같은
        # 머리글·20자 미만 소제목이면 끊긴 문장이 아니다
        prev_lines = [l for l in out[-3:] if l.strip()]
        before = prev_lines[-1] if prev_lines else ""
        if before and not re.search(r"[가-힣]", before) and len(prev_lines) >= 2:
            before = prev_lines[-2]
        if len(before.strip()) < 20 or _PART_LINE_RE.match(before.strip()) or re.match(rf"{_PART}\s?\d", before.strip()):
            before = ""
        # 앞 문장이 끊겼다는 정황만으로는 덩어리가 번호보다 많을 때만 건너뛴다 — 모자라면 첫 덩어리도
        # 새 항목이다(실리콘밸리 0198~0202 실측: '저는 여러분이…'를 꼬리로 보내 0201·0202 가 비었다)
        if starts and (_BOUND_HEAD_RE.match(lines[starts[0]].strip())
                       or (before and not _SENT_END_RE.search(before) and starts[0] == j
                           and len(starts) > len(vals))):
            starts = starts[1:]
        k_n = len(vals)
        gap = vals[0] - prev_num - 1 if prev_num is not None and 0 < vals[0] - prev_num <= 5 else 0
        offset = min(gap, len(starts) - k_n) if len(starts) > k_n else 0
        nums = [_NUM_LINE_RE.match(lines[k]).group(1) for k in range(i, j)]
        assign = dict(zip(starts[offset:], nums))
        left = nums[len(assign):]   # 붙일 덩어리가 모자라면 남은 번호는 덩어리 뒤에 그대로(순서 유지)
        for k in range(j, end):
            out.append(f"{assign[k]} {lines[k].lstrip()}" if k in assign else lines[k])
        out.extend(left)
        moved += len(assign)
        prev_num = vals[-1]
        i = end
    return "\n".join(out), moved


def is_numbered_book(text: str) -> bool:
    """연속 번호 항목(1, 2, 3 …)이 _ENTRY_MIN_CHAIN개 이상 이어지는 '번호 항목 책'(명언집 등)인가."""
    return len(_entry_chain(text)) >= _ENTRY_MIN_CHAIN


def paragraph_entries(text: str) -> tuple[str, int]:
    """연속 번호 항목(0451, 0452 …)마다 앞에 빈 줄을 넣어 단락을 복원한다.
    번호 바로 앞에 같은 줄로 붙은 쪽번호('157 453')는 함께 뗀다. (텍스트, 단락 수)"""
    chain = _entry_chain(text)
    if len(chain) < _ENTRY_MIN_CHAIN:
        return text, 0
    # 항목 번호 연쇄에 속하지 않는, 숫자만 있는 줄 중 바로 앞 항목 번호보다 확연히 작은 수 = 홀로 남은
    # 쪽번호('0238 …' 뒤의 '91') — 지운다. 항목 번호와 쪽번호 값대가 겹치는 책(기독교명언 앞부분)은
    # 구분할 수 없으므로 남긴다
    pos = [m.start() for m in chain]
    vals = [int(m.group(1)) for m in chain]

    def _keep(m: re.Match) -> str:
        k = _bisect(pos, m.start(1))
        if k < len(pos) and pos[k] == m.start(1):
            return m.group(0)
        prev = vals[k - 1] if k > 0 else None
        v = int(m.group(1))
        return "" if prev is not None and v <= prev * 0.6 else m.group(0)

    # 쪽번호 꼴: 앞 줄은 본문, 뒤는 빈 줄·끝 — 단락 끝에 홀로 붙은 숫자 줄만 본다
    text = re.sub(r"(?m)(?<=[^\d\s]\n)[ \t]*(\d{1,3})[ \t]*(?=\n\s*\n|\Z)", _keep, text)
    chain = _entry_chain(text)
    out, last = [], 0
    for m in chain:
        before = text[last:m.start()]
        # 같은 줄에서 번호 바로 앞이 쪽번호뿐이면('157 453', '• 272') 그 쪽번호를 뗀다
        before = re.sub(r"(^|\n)[^\S\n]*[•·|I]?\s*\d{1,3}[^\S\n]+$", r"\1", before)
        before = before.rstrip(" \t")
        # 번호 바로 앞 줄이 짧은 소제목('습관')이면 앞 항목 꼬리가 아니라 다음 항목의 머리 — 함께 묶는다
        head = ""
        # 쪽 경계에서 앞 쪽 쪽번호가 붙은 '30 고백' 꼴이면 쪽번호를 뗀다(앞 그룹 끝에 매달리지 않게)
        tm = re.search(r"\n(?:\d{1,3}\s+)?([가-힣][가-힣 ]{0,11})\n?$", before)
        if tm and before[:tm.start()].strip():
            head, before = tm.group(1) + "\n", before[:tm.start()]
        out.append(before)
        out.append("\n\n" + head)
        last = m.start()
    out.append(text[last:])
    res = re.sub(r"[ \t]+\n", "\n", "".join(out))
    res = re.sub(r"\n{3,}", "\n\n", res).lstrip("\n")
    return res, len(chain)
