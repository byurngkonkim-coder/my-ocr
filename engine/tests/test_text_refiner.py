"""text_refiner 테스트 — 01_OCR_스캔파일텍스트추출프로그램 의 회귀 테스트를 옮겨 왔다."""
import sys
from pathlib import Path

_ENGINE = Path(__file__).resolve().parent.parent
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

import text_refiner as tr

SENT = "이것은 충분히 긴 본문 문장으로 마침표로 끝난다."
H = "Chapter 1 새벽, 잠 못 이루는 당신에게"
LONG = ("경영자는 조직의 성과를 책임지는 사람이며 그 성과는 조직 바깥에서 고객에 의해 "
        "판정되므로 내부의 노력만으로는 결코 충분하다고 말할 수 없는데 바로")


def _book():
    return [
        "표제지 텍스트",
        f"해설\n\n{SENT}",
        "상권 차례\n\n해설 집대성 004 머리말 대안 028\n\n1 경영의 등장 043 2 경영 붐 058",
        "하권 차례\n\n29 왜 경영자가 필요한가 30 무엇이 경영자를 만드는가\n\n주석\n참고문헌",
        f"머리말\n\n전제에 대한 대안\n\n{SENT}",
        f"28장\n1) {SENT}",
        "KI신서 1546\n\n피터 드러커의 매니지먼트(상)\n\n1판 1쇄 인쇄 2008년\n\n"
        "펴낸이 김영곤 펴낸곳 북이십일\n\n책값은 뒤표지에 있습니다 ISBN 978-89-509-1605-3",
    ]


def _long_book(front, back):
    return front + [f"{k}장 {SENT}" for k in range(60)] + back


# ── 부속물 삭제 ──
def test_removes_toc_pages_and_colophon_keeps_content():
    out, notes = tr.strip_book_matter(_book())
    joined = "\n".join(out)
    assert "차례" not in joined and "ISBN" not in joined
    assert "머리말" in joined and "28장" in joined and "해설" in joined
    assert len(out) == 4 and len(notes) == 2


def test_partial_colophon_keeps_preceding_notes():
    pages = [SENT, f"28장\n1) {SENT}\n\n책값은 뒤표지에 있습니다 ISBN 978-89-509-1605-3"]
    out, notes = tr.strip_book_matter(pages)
    assert "28장" in out[-1] and "ISBN" not in out[-1]
    assert notes and "판권" in notes[0]


def test_toc_word_inside_body_sentence_is_kept():
    pages = [f"본문 {SENT}", "이 책의 차례를 보면 알 수 있다.", SENT]
    out, notes = tr.strip_book_matter(pages)
    assert out == pages and notes == []


def test_front_colophon_block_cut_keeps_notes_on_same_page():
    page = (f"일러두기\n\n• {SENT}\n\n1판 1쇄 발행 2014 | 발행처 포이에마 | 발행인 김도완\n\n"
            "ISBN 978-89-97760-68-8")
    out, notes = tr.strip_book_matter(_long_book([page], []))
    assert "일러두기" in out[0] and "ISBN" not in out[0] and "발행처" not in out[0]
    assert any("판권" in n for n in notes)


def test_book_ad_pages_removed_front_and_back():
    ad = "고흐 그림여행\n\n최상운 지음 | 296쪽 | 14,000원"
    out, notes = tr.strip_book_matter(_long_book([ad], [ad]))
    assert "14,000원" not in "\n".join(out) and len(out) == 60
    assert any("도서 광고" in n for n in notes)


def test_colophon_on_second_to_last_page():
    colophon = "KI신서 467\n\n펴낸곳 북21 펴낸이 김영곤\n\nISBN 89-509-0533-7"
    out, _ = tr.strip_book_matter(_long_book([], [colophon, "세미나 안내 광고"]))
    assert "ISBN" not in "\n".join(out)


def test_ocr_toc_without_blank_lines_removed():
    # OCR 결과는 문단 사이 빈 줄이 없다 — 차례 머리글·항목이 본문 문단으로 남으면 안 된다
    page = f"차례\n1장 경영의 등장 043\n2장 경영 붐 058\n{SENT}"
    out, notes = tr.strip_book_matter([page] + [SENT] * 5)
    assert out[0] == SENT and notes


# ── 러닝헤더·쪽번호 ──
def test_repeated_footer_removed_except_first():
    out = tr.strip_running_headers([f"본문 {i}쪽 문장입니다.\n{H}" for i in range(6)])
    assert out[0].endswith(H) and all(H not in p for p in out[1:])


def test_header_below_threshold_untouched():
    pages = [f"본문 {i}쪽 문장입니다.\n{H}" for i in range(4)]
    assert tr.strip_running_headers(pages) == pages


def test_single_word_with_number_not_collapsed():
    pages = [f"Chapter {i}\n본문 {i}." for i in range(1, 8)]
    assert tr.strip_running_headers(pages) == pages


def test_title_page_footer_removed_at_edge_only():
    pages = ["본문 문장.\n\n머리말 | 029", "028 | 피터 드러커의 매니지먼트\n본문.", "가 | 1 같은 본문 줄\n끝."]
    out = tr.strip_running_headers(pages)
    assert out[0] == "본문 문장.\n" and out[1] == "본문." and out[2] == pages[2]


def test_page_number_edge_lines_removed_middle_kept():
    out = tr.strip_page_numbers(["12\n본문 문장.\n2024\n본문 끝.\n- 13 -"])
    assert out == ["본문 문장.\n2024\n본문 끝."]


# ── 끊긴 줄·문장 잇기 ──
def test_rejoins_sentence_split_across_paragraphs():
    text, n = tr.rejoin_split_sentences(f"{LONG} 그\n\n것이다. 다음 문장.")
    assert n == 1 and "\n" not in text and "바로 그 것이다." in text


def test_glues_split_word():
    text, _ = tr.rejoin_split_sentences(f"{LONG} 상\n상력을 넓혀 주었다.")
    assert "상상력을" in text


def test_keeps_headings_and_finished_sentences():
    src = f"{LONG}.\n다음 문장이다.\n\n{LONG}\n\n제2장 경영의 과업\n\n본문."
    text, n = tr.rejoin_split_sentences(src)
    assert n == 0 and text == src


def test_join_broken_lines_in_page():
    assert tr.join_broken_lines("경영자는 조직의 성과를\n책임지는 사람이다.\n제2장") == \
        "경영자는 조직의 성과를 책임지는 사람이다.\n제2장"


# ── 문자 정규화·잡음 (파일형식변환기_MD 에서 옮긴 규칙) ──
def test_normalize_chars_fixes():
    assert tr.normalize_chars("탈출한다（금요일 아침），다음") == "탈출한다(금요일 아침),다음"
    assert tr.normalize_chars("Ａ１​가\xa0나") == "A1가 나"
    assert tr.normalize_chars("1O0 1l2 shouId ﬁle") == "100 112 should file"
    assert tr.normalize_chars("ㅋㅋ 웃음") == "ㅋㅋ 웃음"            # 호환 자모 보존 (NFKC 미사용)


def test_normalize_chars_preserves():
    for s in ["item2 videoClip m2", "12B34 3S4 v1beta", "No, etc, ->", "ID I am"]:
        assert tr.normalize_chars(s) == s, s


def test_remove_noise_lines():
    assert tr.remove_noise_lines("본문 줄\nl\n柴\n•■\n\n12\n가나") == "본문 줄\n\n12\n가나"


def test_fix_hyphenated_words():
    assert tr.fix_hyphenated_words("infor-\nmation") == "information"
    assert tr.fix_hyphenated_words("서울-\n부산") == "서울-\n부산"
    assert tr.fix_hyphenated_words("COVID-\n19") == "COVID-\n19"


def test_fix_punctuation_spacing():
    assert tr.fix_punctuation_spacing("끝났다 .다음 ( 괄호 )") == "끝났다. 다음 (괄호)"
    assert tr.fix_punctuation_spacing("했다.그리고") == "했다. 그리고"
    for s in ["www.naver.com 참고", "e.g. 예", "3.5배와 1,000원"]:
        assert tr.fix_punctuation_spacing(s) == s, s


# ── 통합 ──
def test_refine_pages_end_to_end():
    pages = [f"{i + 1}\n본문 {i}쪽의 첫 줄은 길게 이어지고\n다음 줄에서 끝난다.\n{H}" for i in range(6)]
    text, notes = tr.refine_pages(pages)
    assert H in text and text.count(H) == 1           # 첫 등장만 보존
    assert "본문 0쪽의 첫 줄은 길게 이어지고 다음 줄에서 끝난다." in text
    assert "\n1\n" not in f"\n{text}\n" and notes == []


def test_refine_pages_joins_word_split_across_lines():
    # 줄 끝에서 끊긴 어절은 문서 안에 붙은 형태가 있으면 붙인다
    pages = ["이 사실은 분명하다. 그 사실은 중요하다.",
             "아무도 잘못된 결단을 원하지 않는다는 사\n실은 주지의 사실이다."]
    text, _ = tr.refine_pages(pages)
    assert "는다는 사실은 주지의" in text


def test_refine_pages_keeps_spaced_name_majority():
    # 띄어 쓴 형태가 더 많으면 OCR이 가끔 붙여 읽은 형태로 바꾸지 않는다
    pages = ["스펜서 존슨 지음. 스펜서 존슨 박사. 스펜서 존슨의 책. 스펜서존슨 스펜서존슨"]
    text, _ = tr.refine_pages(pages)
    assert text.count("스펜서 존슨") == 3


def test_refine_pages_drop_matter_reports():
    text, notes = tr.refine_pages(_book(), drop_matter=True)
    assert "ISBN" not in text and "차례" not in text and len(notes) == 2


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ALL {len(tests)} TESTS PASSED SUCCESSFULLY!")
