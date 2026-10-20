"""noise_rules 테스트 — 01_OCR_스캔파일텍스트추출프로그램 engine/tests/test_noise_rules.py 를 옮겨 왔다
(2026-10-16 명언집 5권 실측 사례, 2026-10-20 재동기화). 본문은 원본과 같고 import·실행부만 MyOCR 방식이다."""
import sys
from pathlib import Path

_ENGINE = Path(__file__).resolve().parent.parent
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

import noise_rules as nr



def test_garbage_lines_removed_but_numbers_and_words_kept():
    page = "\n".join([
        "V•니 G ' ;，",                 # 장식 그림을 읽은 잡음
        "■次'系懸",
        "HMAi.乂 또,'",
        "• 272",                        # 항목 번호 — 남긴다
        "소망",                          # 짧은 소제목 — 남긴다
        "I believe in innovation.",
    ])
    out = nr.clean_page(page).splitlines()
    assert out == ["272", "소망", "I believe in innovation."]


def test_noise_tokens_stripped_inside_body_line():
    assert nr.clean_page("艾^ 하루에 한개 팔고 ᄀ 하지만 ■■■nr내게 ______ 끝이다.") \
        == "하루에 한개 팔고 하지만 nr내게 끝이다."
    # 한글 어절 앞에 붙은 한자 오인식은 떼고 어절은 남긴다
    assert nr.clean_page("一오스틴 펠프스(1820-1890), 목사") == "오스틴 펠프스(1820-1890), 목사"
    # 한자 병기는 본문
    assert nr.clean_page("옛 것을 익히는 온고지신 (溫故知新) 의 자세") == "옛 것을 익히는 온고지신 (溫故知新) 의 자세"


def test_body_line_with_symbol_junk_keeps_hangul():
    assert nr.clean_page("/1 r* xr \\ x - 나항상 선택의 갈림길이 있다") == "나항상 선택의 갈림길이 있다"


def test_fullwidth_and_broken_entry_numbers():
    assert nr.clean_page("OO52 우리의 성공（파트너십）：시작") == "0052 우리의 성공(파트너십):시작"
    assert nr.clean_page("0 0 5 1 지금은 환상적인 시기입니다") == "0051 지금은 환상적인 시기입니다"


def test_one_word_english_lines_joined():
    page = "0052 우리의 성공은 파트너십이다 Our\nsuccess\nhas\nreally\nbeen\nbased."
    assert nr.clean_page(page) == "0052 우리의 성공은 파트너십이다 Our success has really been based."


def test_garbled_running_headers_removed_without_body_loss():
    # 쪽마다 조금씩 다르게 깨져 읽힌 머리글(실측 꼴) + 쪽마다 다른 본문
    variants = ["실리콘밸리 천재들의 생각 아포리즘", "실리콘밸리 천재들의 생각 아포리증",
                "실라콘밸리 천재들의 생각 아포리즘", "실리콘밸리천재들의생각아포리즘"]
    bodies = ["기회는 준비된 사람에게 온다", "실패는 배움의 다른 이름이다", "고객이 모든 것을 결정한다",
              "혁신은 빠르게 진행되고 있다", "작게 시작해서 크게 키워라", "질문이 답을 바꾼다"]
    pages = [f"{100 + i} {variants[i % 4]}\n0{100 + i} {bodies[i % 6]} ({i})" for i in range(12)]
    pages.append("그렇지 않다면 당 74 실리콘밸리 천재들의 생각 아포리즘")
    out, n = nr.strip_fuzzy_headers(pages)
    assert n >= 12
    assert all("아포리" not in p for p in out)
    assert out[0].splitlines() == ["0100 기회는 준비된 사람에게 온다 (0)"]
    assert out[-1] == "그렇지 않다면 당"


def test_fuzzy_header_removal_keeps_body_words():
    # 가장자리에 자주 오는 이름('아이젠하워')이 머리글 조각으로 배워져 본문 낱말·문장 가운데를 지우던 것
    long = "그는 부하들을 아꼈고 동료들과도 잘 지냈다. 그런가 하면 " * 3
    pages = [f"{100 + i} 아이젠하워는 장군이었다\n{long}\n쪽 {i} 끝" for i in range(8)]
    pages.append(f"첫 줄\n둘째 줄\n가운데 문단 아이젠하워에게는 자석 같은 매력이 있었다\n셋째 줄\n넷째 줄")
    pages.append(f"{long}아이젠하워는 장군이었다 하는 말이 문단 가운데에 있다. {long}")
    out, _ = nr.strip_fuzzy_headers(pages)
    assert "아이젠하워에게는 자석" in out[-2]
    assert "아이젠하워는 장군이었다 하는 말이" in out[-1]


def test_part_header_with_page_number_removed_mid_line():
    pages = ["Give me a turbulent world and I'll take the PART 3 실라콘뱅리 혁신가들의 통학 331 turbulent one."]
    out, _ = nr.strip_fuzzy_headers(pages)
    assert out[0] == "Give me a turbulent world and I'll take the turbulent one."


def test_paired_guillemets_kept_but_stray_ones_removed():
    # 하루명화하루명언 실측: '«대지»의 작가' 의 여는 '«'를 장식 기호로 지웠다 — 짝 맞는 책 제목 표시는 본문
    for line in ["«대지»의 작가 펄 백의 말입니다.", "샘을 길어 올린다. «대지»의 작가 필 백의 말입니다.",
                 "«대지»", "«대지의 노래»를 읽었다"]:
        assert nr.clean_page(line) == line, line
    assert nr.clean_page("« 하루에 한개 팔고 끝이다.") == "하루에 한개 팔고 끝이다."   # 홀로 떨어진 '«'는 잡음
    assert nr.clean_page("«하루에 한개 팔고 끝이다.") == "하루에 한개 팔고 끝이다."    # 짝 없는 '«'도 잡음


def test_toc_chapter_labels_kept():
    # 하루명화하루명언 실측: 2단 차례 머리 'Chapter 2 Chapter 3 …'의 두 번째 번호를 쪽번호로 보고 지웠다
    line = "Chapter 2 Chapter 3 아침, 오후, 일어나 시작하는 당신에게 다시 살아가는 당신에게"
    assert nr.strip_fuzzy_headers([line]) == ([line], 0)
    # 줄 구조가 살아 있는 차례 쪽 — 장 제목 뒤 쪽번호는 머리글이 아니라 차례 항목
    toc = "\n".join(["차례", "Chapter 1 새벽, 잠 못 이루는 당신에게 14"]
                    + [f"제목 {k}번째 이야기{20 + 6 * k}" for k in range(12)])
    assert nr.strip_fuzzy_headers([toc])[0] == [toc]


def test_entry_paragraphs_restored_and_page_numbers_dropped():
    body = []
    for k in range(1, 41):
        body.append(f"{k:04d} 명언 {k}번 문장이다")
        if k == 20:
            body.append("157 0021 같은 줄에 붙은 쪽번호와 다음 항목")
    text = " ".join(body).replace("0021 명언 21번", "0022 명언 22번", 0)
    out, n = nr.paragraph_entries(text)
    paras = out.split("\n\n")
    assert n >= 30
    assert paras[0] == "0001 명언 1번 문장이다"
    assert "157" not in out.split("0021")[0].split("\n\n")[-1]


def test_book_matter_rules_drop_front_matter_but_keep_prologue_and_body():
    toc = "\n".join(f"장 제목 {k}\n{k * 10}" for k in range(1, 10))
    body_entry_page = "\n".join(f"감사\n{k}\n감사하는 삶이 더 좋은 것이다." for k in range(5))
    pages = (
        ["실리콘밸리 천재들의 생각 아포리즘\nOf Silicon Valley",                      # 표지
         "저자 손호성\n아르고나인 미디어그룹 대표이사\n국내 최초로 스도쿠를 개발했다.",   # 저자 소개
         "성경, 왜 믿어야하는가 케빈 드영 지음 | 175면 | 11.000원",                    # 다른 책 광고
         "Korean Edition Copyright © 2020\n저작권법에 의하여 보호를 받는 저작물이다",    # 저작권
         "PROLOGUE\n이 책은 짧은 문장이다. 그래서 오래 남는다. 천천히 읽기 바란다.",       # 머리말 — 본문
         toc, toc]
        + [f"{k:04d} 본문 명언 {k}번째 문장은 그대로 남는다." for k in range(1, 30)]
        + [body_entry_page])                                                           # 본문 속 '번호/짧은 줄' 쪽
    notes: list[str] = []
    out = nr.drop_book_matter_rules(pages, notes)
    assert [bool(p.strip()) for p in out[:7]] == [False, False, False, False, True, False, False]
    assert all(p.strip() for p in out[7:])
    assert any("저자 소개" in n for n in notes) and any("차례" in n for n in notes)


def test_stacked_margin_numbers_moved_to_their_quotes():
    # 시네마명언 23쪽 실측: 쪽번호 '19'가 붙은 번호 몰림, 013 은 OCR 이 놓침, 뒤에 소제목
    text = "\n".join([
        "012 우리의 인생도 선택에 따라 달라질 수 있어.",
        "Also does our life select how to follow.",
        "PART 1 꿈과 자유를 찾아주는 명대사",
        "19 014",
        "015",
        "과거는 뒤에 둬야 앞으로 나갈 수 있어.",
        "You got to put the past behind you.",
        "제 운명은 뭐예요? / 그건 너가 알아서 해야 해.",
        "Whats my destiny? / It's up to you.",
        "저마다 운명이 있는지 아니면 그냥 떠도는 건지 모르겠어 I don't know if we each have a destiny.",
        "희망은",
        "사라지지 않는다",
    ])
    out, moved = nr.redistribute_number_stacks(text)
    lines = out.split("\n")
    assert moved == 2
    assert "014 제 운명은 뭐예요? / 그건 너가 알아서 해야 해." in lines
    assert any(l.startswith("015 저마다 운명이") for l in lines)
    assert "과거는 뒤에 둬야 앞으로 나갈 수 있어." in lines      # 놓친 013 몫 — 번호를 지어내지 않는다
    assert "19 014" not in out and "사라지지 않는다" in lines


def test_stacked_numbers_skip_previous_entry_tail_and_split_glued_numbers():
    # 실리콘밸리 25·27쪽 실측: 앞 문장 끝에 붙은 번호들, 앞 항목 꼬리(조사로 시작·영어 번역 줄)
    text = "\n".join([
        "0026 내가 17살 때, 이런 인용구를 읽은 적이 있다 만약 당신이 하루하루를 마지막 날인 0027",
        "0028",
        "니.” 일 때마다 나는 변화해야 한다고 느꼈다",
        "When I was 17, I read a quote that went something like this.",
        "여러분의 일은 여러분의 인생의 큰 부분이 될 것이고 진정으로 만족하는 방법은 하나다",
        "Your work is going to fill a large part of your life.",
        "나는 하루 이틀이 아닌 수년을 최소 단위로 생각하는 것에 익숙해졌다",
    ])
    out, moved = nr.redistribute_number_stacks(text)
    lines = out.split("\n")
    assert moved == 2
    assert lines[1] == "니.” 일 때마다 나는 변화해야 한다고 느꼈다"
    assert lines[3].startswith("0027 여러분의 일은")
    assert lines[5].startswith("0028 나는 하루 이틀이")


def test_number_line_with_trailing_letter_o_is_read_as_number():
    # 실리콘밸리 38쪽 실측: 'OO7O' 가 숫자로 안 읽혀 0068~0071 몰림이 끊겼다
    assert nr.clean_page("OO68\nOO69\nOO7O\nOO71").splitlines() == ["0068", "0069", "0070", "0071"]


def test_stack_with_too_few_quotes_does_not_skip_first_quote():
    # 실리콘밸리 80쪽 실측: 앞 문장이 끊긴 정황만으로 첫 명언을 꼬리로 보내 0201·0202 가 비었다
    text = "\n".join([
        "0197 많은 사람과 함께 일할 방법을 생각해내야",
        "0198", "0199", "0200",
        "저는 여러분이 세상을 바꾸는 일에 관심이 있습니다",
        "I'm interested in things that change the world.",
        "우리에겐 의식의 빛을 비출 의무가 있다 어떤 일이 지속할 수 있는지",
        "I think we have a duty to maintain the light.",
    ])
    out, moved = nr.redistribute_number_stacks(text)
    lines = out.split("\n")
    assert moved == 2
    assert lines[1].startswith("0198 저는 여러분이") and lines[3].startswith("0199 우리에겐")
    assert lines[-1] == "0200"          # 붙일 곳이 없는 번호는 본문 뒤에(순서 유지)


def test_lone_page_number_at_paragraph_end_removed():
    entries = [f"{k:04d} 명언 {k}번 문장이다." for k in range(200, 240)]
    entries[38] += "\n두 종류의 회사가 있다.\n91\n"         # 0238 뒤 쪽번호 + 쪽 경계 빈 줄
    out, _ = nr.paragraph_entries("\n".join(entries))
    assert "\n91" not in out and "두 종류의 회사가 있다." in out


# ── 일반화 점검: 명언집 밖 문서에서 본문을 해치지 않는가 ─────────────────────────────

def test_classical_chinese_quote_lines_kept():
    page = "공자는 이렇게 말했다.\n學而時習之 不亦說乎\n배우고 때로 익히면 기쁘지 아니한가."
    assert nr.clean_page(page).splitlines()[1] == "學而時習之 不亦說乎"


def test_long_classical_lines_and_leading_hanja_words_kept():
    # 한자 낱말 3개↑ 한문 줄을 낱말 품질 미달로 지우던 것(손자병법 실측: 한자 184→74자)
    for line in ("學而時習之 不亦說乎 有朋自遠方來",
                 "夫未戰而廟算勝者, 得算多也. 未戰而廟算不勝者, 得算少也.",
                 "知彼知己 百戰不殆 不知彼而知己 一勝一負",
                 "夫, 未戰而廟算勝者, 得算多也. 未戰而廟算不勝者, 得算少",
                 "也.多算勝, 少算不勝, 而況於無算乎.吾以此觀之, 勝負見矣."):
        assert nr.clean_page(line) == line
    # 줄 앞 한자 낱말·줄바꿈으로 갈린 한자 병기의 뒷부분
    assert nr.clean_page("佛經, 현실을 꿀맛 젖은 사형수에 비유") == "佛經, 현실을 꿀맛 젖은 사형수에 비유"
    assert nr.clean_page("인재(人\n災)란 점이 밝혀졌습니다.") == "인재(人\n災)란 점이 밝혀졌습니다."
    # 괄호 없는 한자 병기(로컬 한자 읽기 결과) — 한글 뒤·한글 사이
    assert "般若心經" in nr.clean_page("불교의 «반야심경般若心經 외에 다른 것이 별로 없을 것이다.")
    assert "新井白石" in nr.clean_page("손자해석가의 한 사람인 아라이하쿠세 키新井白石가 그의 책에서 남긴 말")


def test_number_repairs_do_not_touch_ordinary_text():
    assert nr.clean_page("혈중 O2 농도가 낮아졌다") == "혈중 O2 농도가 낮아졌다"
    assert nr.clean_page("만족도 척도 1 2 3 4 5 중 하나를 고른다") == "만족도 척도 1 2 3 4 5 중 하나를 고른다"
    assert nr.clean_page("그는 1 9 8 3 년에 태어났다") == "그는 1983 년에 태어났다"
    assert nr.clean_page("원칙을 9 8 퍼센트의 시간보다 1 0 0 퍼센트 지킨다") == "원칙을 98 퍼센트의 시간보다 100 퍼센트 지킨다"


def test_price_in_preface_is_not_ad():
    pages = ["머리말\n저 역시 처음에는 10,000원짜리 책 한 권으로 시작했다. 그 경험을 나누고 싶다."] \
        + [f"본문 {k}쪽의 문장이다. 계속 이어진다." for k in range(20)]
    notes: list[str] = []
    out = nr.drop_book_matter_rules(pages, notes)
    assert out[0] == pages[0] and not notes


def test_numbered_paragraph_rules_only_for_numbered_books():
    prose = "분기별 실적은 다음과 같다\n1\n2\n3\n매출이 크게 늘어난 분기였다\n이익도 함께 늘었다\n원가는 줄었다"
    assert not nr.is_numbered_book(prose)
    assert nr.is_numbered_book("\n".join(f"{k:04d} 명언 {k}번이다." for k in range(1, 40)))


def test_korean_chapter_reference_in_prose_not_removed():
    bodies = [f"{k}번째 쪽의 본문은 서로 다른 내용을 담고 있다 {'가나다라마바'[k % 6]}" for k in range(12)]
    pages = bodies[:5] + ["제2장에서 살펴본 바와 같이 3가지 원칙이 중요하다."] + bodies[5:]
    out, _ = nr.strip_fuzzy_headers(pages)
    assert out[5] == pages[5]


def test_korean_and_english_sentences_on_one_line_are_split():
    line = "0264 모든 사람에게 모든 것을 공유할 수 있는 힘을 주어라 Give everyone the power to share anything with anyone."
    assert nr.split_language_lines(line) == (
        "0264 모든 사람에게 모든 것을 공유할 수 있는 힘을 주어라\n"
        "Give everyone the power to share anything with anyone.", 1)
    two = ("채용은 핵심 역량입니다 절대로 아웃소싱해서는 안 됩니다 Recruiting is a core competency. "
           "It should never be outsourced. 다음 명언은 회사의 사명에 관한 것이다")
    out, n = nr.split_language_lines(two)
    assert n == 2 and out.split("\n")[1] == "Recruiting is a core competency. It should never be outsourced."


def test_inline_english_quotes_and_terms_not_split():
    for line in ["스티브 잡스는 졸업식 연설에서 “Stay hungry, stay foolish.”라고 말했다",
                 "애플의 광고 문구인 Think Different Campaign을 통해 브랜드를 바꿨다",
                 "구글은 기술 기업이다 OK Google",
                 "넷플릭스는 인터넷의 넷(Net)과 영화를 뜻하는 플릭스(Flix)를 합친 말이다"]:
        assert nr.split_language_lines(line) == (line, 0), line


def test_citation_brackets_repaired():
    cases = {
        "〈렛미인(Let The Right One In)>(2008)": "〈렛미인(Let The Right One In)〉(2008)",
        "〈용서받지 못한 자X2005)": "〈용서받지 못한 자〉(2005)",
        "〈라라랜드(La La Land)X20l6)": "〈라라랜드(La La Land)〉(2016)",
        "<블레이드 러너 2049〉(2017)": "〈블레이드 러너 2049〉(2017)",
        "_제임스 클리어,「아주 작은 습관의 힘j": "_제임스 클리어,「아주 작은 습관의 힘」",
        "『길매기의 꿈J": "『길매기의 꿈』",
        "-토마스 아 켐피스(1380-1471), r그리스도를 본받아」저자": "-토마스 아 켐피스(1380-1471), 「그리스도를 본받아」저자",
        "「파리대왕』은 노벨문학상 수상작으로": "「파리대왕」은 노벨문학상 수상작으로",
    }
    for src, want in cases.items():
        assert nr.repair_quote_brackets(src)[0] == want, src


def test_brackets_left_alone_when_ambiguous():
    for line in ["가격이 3 < 5 이고 Raj 씨가 왔다", "〈빌리 엘리어트〉의 빌리는", "x > y 이면 r 값이 크다",
                 "그중 일부를 발췌한 내용이 <표 1>이다", "1987년 출간한 <무형자산>에서"]:
        assert nr.repair_quote_brackets(line) == (line, 0), line


def test_space_after_comma_between_hangul():
    assert nr.clean_page("어느 날，첫사랑의 부고를 들었다") == "어느 날, 첫사랑의 부고를 들었다"
    assert nr.clean_page("중요한 건,원치 않는 일에 매달리지 않는 것이다") == "중요한 건, 원치 않는 일에 매달리지 않는 것이다"
    assert nr.clean_page("매출은 1,000억 원, 이익은 a,b 순이다") == "매출은 1,000억 원, 이익은 a,b 순이다"


def test_attributed_quote_groups_split_even_without_numbers():
    # 기독교명언 31쪽 실측: 47·48 번호를 OCR 이 못 읽음 — 저자 출처 줄로 그룹 경계를 잡는다
    page = "\n".join([
        "고난", "46",
        "우리 인생의 어두운 순간들은 필요한 만큼만 지속될 것이다.",
        "一찰스 스탠리(1932-), 애틀랜타 제일침례교회 담임 목사성경의 진리",
        "요셉은 형들에게 이렇게 말했다. “당신들은 나를 해하려 하였으나(창",
        "심장 발작을 비롯한 육체적 질병의 대단히 많은 경우가 우리 죄에서 비롯된다.",
        "一A. W. 토저(1897-1963), 설교자, 저자",
    ])
    pages = [nr.clean_page(page)] * 30
    out, n = nr.group_attributed_quotes(pages)
    assert n == 30 and nr.GROUP_MARK + "\n심장 발작을" in out[0]   # 쪽 맨 위 그룹(고난·46)은 경계 불필요
    assert out[0].startswith("고난\n46")                            # 주제어·번호는 그 그룹의 머리
    text = nr.finish_groups(out[0])
    assert "(창\n\n심장 발작을" in text and nr.GROUP_MARK not in text


def test_attribution_grouping_off_for_ordinary_documents():
    pages = ["홍길동(1950-2010)은 경영학자였다.\n그는 많은 책을 남겼다."] * 5
    assert nr.group_attributed_quotes(pages) == (pages, 0)


def test_design_page_noise_cleaned():
    page = "\n".join(["자다가", "일어날", "정도로", "두근거리는", "일이", "생겼다면", "당장", "해야한다.",
                      "- 네’ 기직포*. 드’,r",
                      "‘ 휴/ ,' : ‘ < : : ' < 성노, 급격 히 추락하거나 상승하는 사람은 별로 없다",
                      "고난", "그 사람은 끝까지 해낸다."])
    assert nr.clean_page(page).splitlines() == [
        "자다가 일어날 정도로 두근거리는 일이 생겼다면 당장 해야한다.",
        "성노, 급격 히 추락하거나 상승하는 사람은 별로 없다",
        "고난", "그 사람은 끝까지 해낸다."]


def test_low_quality_line_keeps_real_sentence():
    assert nr.clean_page("가,' 무시당한다.") == "가, 무시당한다."
    assert nr.clean_page("1 - I_ _ 낸거야!”").endswith("낸거야!”")   # 앞 '1'은 항목 번호일 수 있어 남김
    assert nr.clean_page("-J. H. 에번스") == "-J. H. 에번스"          # 저자 출처 줄
    assert nr.clean_page("| 우정의 끝").endswith("우정의 끝")          # 항목 제목(구분자 + 한 글자 낱말)
    assert nr.clean_page("I 기본의 힘") == "I 기본의 힘"
    # 디지털 문서 실측(평가 도구가 찾은 손실): 기고자 표시·한자 병기·한문 인용
    assert nr.clean_page("206호(2016년 8월 lssue 1) / 한숙기") == "206호(2016년 8월 lssue 1) / 한숙기"
    assert nr.clean_page("건(件) 단위로 기억하는 프로세스") == "건(件) 단위로 기억하는 프로세스"
    assert "患人之不知己患不知人也)." in nr.clean_page("患人之不知己患不知人也).” (<논어> 학이편)")
    for line in ["[표1] 인터넷 리뷰에 대한 일반 국민과 트위터 사용자 비교",
                 "‘1%만 바꾸면 된다’는 삶의 철학을 널리 퍼트렸다.",
                 "㎝도 안 되지만 가슴속에 담아둔 생각", "5월 9일 - 10일", "합 계", "의 견",
                 "③PT가 있는 사업이면 프레젠테이션에서 승부하라.", "甲이 요청한 순서·제목",
                 "표제에 좋은 것:", "‘짜증나는 델’이 ‘쿨∼한 델’로 대변신"]:
        assert nr.clean_page(line) == line, line
    for junk in ["7혓", "r- 5못", "1사", "2야 568", "돈 나"]:     # 숫자+아무 글자·띄어 쓴 조각은 잡음
        assert nr.clean_page(junk) == "", junk


def test_short_number_runs_are_not_paragraphed():
    text = "1984년에 3. 가지 원칙과 2 3 4 를 말했다."
    assert nr.paragraph_entries(text) == (text, 0)


def test_strip_page_numbers_follows_page_sequence():
    # 쪽 끝 쪽번호(이웃 쪽과 1씩 이어짐)만 뗀다 — 2026-10-17 길을묻는그대에게 '61 어떤 단계에서든' 실측
    pages = [f"본문 {k}쪽 문장이다.\n그러" + ("\n" if k % 2 else " 길을 묻는 그대에게 ") + str(60 + k) for k in range(5)]
    out, n = nr.strip_page_numbers(pages)
    assert n == 5
    assert all(not o.rstrip().endswith(str(60 + k)) for k, o in enumerate(out))
    assert out[1].endswith("그러") and out[0].endswith("그러 길을 묻는 그대에게")


def test_strip_page_numbers_keeps_item_numbers_and_labels():
    # 쪽마다 항목이 3개씩 — 숫자만 있는 항목 번호 줄은 쪽 순서와 맞지 않아 남긴다(1001가지기독교명언 실측)
    items = ["\n".join([str(3 * k + 1), "명언 하나", str(3 * k + 2), "명언 둘", str(3 * k + 3)]) for k in range(5)]
    assert nr.strip_page_numbers(items) == (items, 0)
    # 장 표지 'PART 2'·괄호 숫자·줄 머리 항목 번호는 쪽번호가 아니다
    words = ["인간들을 볼 때면", "나 여기 있어", "진실은 진실된", "용서란 타인에게", "다른 식당에 갔다면"]
    pages = [f"PART {k}\n본문 문장\n({k}) 항목\n{100 + k} {words[k]}" for k in range(5)]
    assert nr.strip_page_numbers(pages) == (pages, 0)


def test_latin_attribution_lines_open_groups_and_stay_apart():
    # '• 한글이름 Latin Name, 1706~1790.' 꼴 저자 줄(괄호 없음) — 대한민국CEO명언집 실측 2026-10-17
    attr = "• 벤저민 프랭클린 Benjamin Franklin, 1706~1790. 미국의 정치가."
    page = "\n".join(["정의가 없는 용기는", "나약하기 짝이 없다.", attr,
                      "충고는 하늘에서 내리는 눈과 같다",
                      "마음 깊은곳까지 파고든다 새뮤얼테일러클리지Samuel Taylor Coleridge,1772-1834, 영국의 시인."])
    out, n = nr.group_attributed_quotes([page] * 30)
    lines = out[0].split("\n")
    assert "- 벤저민 프랭클린 Benjamin Franklin, 1706~1790. 미국의 정치가." in lines      # 표지 '- '로 남김
    assert "- 새뮤얼테일러클리지Samuel Taylor Coleridge,1772-1834, 영국의 시인." in lines   # 붙은 저자 줄 분리
    # 두 줄 명언은 통째로 한 그룹 — 그룹 표지는 둘째 명언 머리에만
    assert lines.index(nr.GROUP_MARK) == lines.index("충고는 하늘에서 내리는 눈과 같다") - 1


def test_year_followed_by_word_starting_with_won_is_not_price():
    # '1770-1823 원수가'(원수=元帥)를 책값으로 보고 본문 쪽을 '다른 책 광고'로 지우던 것(감사 27번)
    body = ("아우스터리츠 전투는 지금의 체코에서 벌어졌는데, 다부Louis-Nicolas Davout, 1770-1823 원수가 "
            "110킬로미터의 거리를 이틀 만에 강행군으로 돌파해서 전장에 도착했다. 『손자병법』 옮김 322쪽.")
    pages = [body] * 40
    out = nr.drop_book_matter_rules(pages, [])
    assert out[3] == body
    ad = "경영의 실제 | 피터 드러커 지음 | 이재규 옮김 | 15,000원"
    assert nr.drop_book_matter_rules([ad] + [body] * 39, [])[0] == ""


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ALL {len(tests)} TESTS PASSED SUCCESSFULLY!")
