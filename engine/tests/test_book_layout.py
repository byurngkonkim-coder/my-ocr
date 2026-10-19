"""book_layout 테스트 — 여백 제거·단락 경계·차례 추출·제목 정비·MD/TXT 출력.
쪽 모양은 21세기지식경영 스캔(2334px 높이) 실측 좌표를 줄여 만든 합성 쪽이다."""
import sys
from pathlib import Path

_ENGINE = Path(__file__).resolve().parent.parent
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

import book_layout as bl

W, H, LH, PITCH, LEFT, RIGHT = 1650, 2334, 50, 70, 260, 1340
BODY = "현실에 대한 기본 가정은 경영과 같은 사회과학의 패러다임을 형성하며 학자와 실무자의"


def _line(text, y, x0=LEFT, x1=RIGHT, h=LH):
    return (text, 1.0, (x0, y, x1, y + h))


_WORDS = ("조직 경영 지식 근로자 생산성 전략 현실 변화 정보 기술 고객 시장 성과 책임 목표 가치 혁신 사회 "
          "경제 인구 교육 기업 정부 제도 판단 결정 실행 측정 관리 의사 소통 협력 신뢰 기여 강점 약점 "
          "시간 자원 투자 위험 기회 미래 과거 원칙 가정 이론 실무 학자 연구 사례 결과 원인").split()


def _varied(n, i):
    """쪽·줄마다 다른 본문 줄 — 비슷한 줄이 반복되면 깨진 머리글 규칙이 본문을 머리글로 본다."""
    import random
    rnd = random.Random(n * 1000 + i)
    return " ".join(rnd.choice(_WORDS) + rnd.choice(("은", "을", "의", "에", "과")) for _ in range(9))


def _body_page(n, footer=None, top_y=280, lines=20, extra=()):
    ls = [_line(_varied(n, i), top_y + i * PITCH) for i in range(lines)]
    ls += list(extra)
    if footer:
        ls.append(_line(footer, 2050, 1100, 1340, 40))
    return {"w": W, "h": H, "lines": ls}


def test_strip_margins_removes_page_numbers_and_running_footers():
    pages = [_body_page(i, f"제1장새로운경영패러다임{27 + 2 * i}" if i % 2 == 0 else str(28 + 2 * i))
             for i in range(6)]
    out, n = bl.strip_margins(pages)
    assert n == 6
    assert all(len(p["lines"]) == 20 for p in out)


def test_strip_margins_keeps_heading_at_top_of_page():
    # 쪽 맨 위 절 제목 — 위치는 머리말과 같지만 반복되지 않으므로 남는다
    head = _line("1.경영은 기업경영이다", 150, 300, 800)
    pages = [_body_page(0, extra=[head])] + [_body_page(i) for i in range(1, 5)]
    out, _ = bl.strip_margins(pages)
    assert any(l[0] == "1.경영은 기업경영이다" for l in out[0]["lines"])


def test_strip_margins_keeps_toc_page_number_on_title_row():
    # 차례 쪽 맨 위 '제1장맺음말 … 88' — 같은 높이에 글줄이 있으면 쪽번호가 아니다
    row = [_line("제1장맺음말", 268, 375, 598), _line("88", 287, 1361, 1418)]
    page = {"w": W, "h": H, "lines": row + [_line(BODY, 420 + i * PITCH) for i in range(15)]}
    out, n = bl.strip_margins([page])
    assert n == 0 and len(out[0]["lines"]) == 17


def test_page_texts_paragraph_break_on_indent_and_gap():
    ls = [_line("첫 단락의 마지막 줄이다.", 280, LEFT, 700),
          _line(BODY, 280 + PITCH, LEFT + 40),          # 들여 쓴 새 단락
          _line(BODY, 280 + 2 * PITCH),
          _line(BODY, 280 + 4 * PITCH)]                 # 줄 높이 이상 벌어짐
    text = bl.page_texts([{"w": W, "h": H, "lines": ls}])[0]
    assert text.split("\n") == ["첫 단락의 마지막 줄이다.", "", BODY, BODY, "", BODY]


def test_page_texts_joins_dropcap():
    ls = [_line("현", 280, LEFT, LEFT + 110, 130), _line("실에 대한 기본 가정은", 290, LEFT + 120)]
    assert bl.page_texts([{"w": W, "h": H, "lines": ls}])[0] == "현실에 대한 기본 가정은"


def _toc_page():
    rows = [("·역자의말/3", None), ("·저자의 말:내일의 핵심문제/15", None),
            ("제1장 새로운 경영 패러다임", None), ("제1장에들어가면서:가정이 중요한 이유", "27"),
            ("1.경영은 기업경영이다", "32"), ("2.단 하나의 올바른 조직", "38"),
            ("제2장 전략—새로운 현실들", None), ("1.감소하는출산율", "96"), ("·감사의말/341", None)]
    ls = [_line("차 레", 450, 680, 920)]
    for i, (t, no) in enumerate(rows):
        y = 900 + i * PITCH
        ls.append(_line(t, y, 300, 900))
        if no:
            ls.append(_line(no, y + 2, 1283, 1332))
    return {"w": W, "h": H, "lines": ls}


def test_extract_toc_levels():
    pages = [_body_page(0), _toc_page()] + [_body_page(i) for i in range(2, 40)]
    entries, toc_pages = bl.extract_toc(pages)
    assert toc_pages == [1]
    got = [(e["level"], e["title"], e["page"]) for e in entries]
    assert got == [(1, "역자의말", 3), (1, "저자의 말:내일의 핵심문제", 15),
                   (1, "제1장 새로운 경영 패러다임", None), (2, "제1장에들어가면서:가정이 중요한 이유", 27),
                   (2, "1.경영은 기업경영이다", 32), (2, "2.단 하나의 올바른 조직", 38),
                   (1, "제2장 전략—새로운 현실들", None), (2, "1.감소하는출산율", 96), (1, "감사의말", 341)]


def test_extract_toc_none():
    assert bl.extract_toc([_body_page(i) for i in range(30)]) == ([], [])


def test_mark_headings_multiline_window_and_inferred_chapter():
    entries = [{"level": 1, "title": "저자의 말:내일의 핵심문제", "page": 15},
               {"level": 1, "title": "제1장 새로운 경영 패러다임", "page": None},
               {"level": 2, "title": "1.경영은 기업경영이다", "page": 32},
               {"level": 2, "title": "2.단 하나의 올바른 조직", "page": 38}]
    pages = [""] * 45
    pages[17] = "저자의 말\n내일의 핵심문제\n" + BODY      # 책 15쪽이 PDF 17쪽 → 차이 +2
    pages[25] = "제\n새로운\n으교"                       # 세로쓰기 장 표제지 — OCR 이 못 읽음
    # 장 첫머리 목록에 절 제목과 같은 문장 — 차례 쪽번호(32+2=34쪽)보다 한참 앞이라 제목이 아니다
    pages[29] = "1.경영은 기업경영이다\n" + BODY
    pages[34] = "1.경영은 기업경영이다\n" + BODY
    pages[40] = "2.단 하나의 올바른 조직\n" + BODY
    stats = {}
    out = bl.mark_headings(pages, entries, [], stats)
    assert stats["found"] == 3 and stats["inferred"] == 1 and stats["missing"] == []
    assert out[17].split("\n")[:3] == ["", "# 저자의 말:내일의 핵심문제", ""]
    assert out[29].startswith("1.경영은 기업경영이다\n")
    assert out[34].split("\n")[:6] == ["", "# 제1장 새로운 경영 패러다임", "", "", "## 1.경영은 기업경영이다", ""]
    assert out[40].startswith("\n## 2.단 하나의 올바른 조직\n")


def test_mark_headings_clears_toc_pages():
    out = bl.mark_headings(["차례 쪽", "본문"], [{"level": 1, "title": "본문 제목", "page": None}], [0], {})
    assert out[0] == ""


def test_output_md_and_txt():
    entries = [{"level": 1, "title": "제1장 시작", "page": None}, {"level": 2, "title": "1.첫 절", "page": 3}]
    body = bl.space_headings("# 제1장 시작\n## 1.첫 절\n본문 문장이다.\n#태그 는 제목이 아니다")
    assert body == "# 제1장 시작\n\n## 1.첫 절\n\n본문 문장이다.\n#태그 는 제목이 아니다"
    md = bl.to_markdown(body, entries)
    assert md.startswith("# 차례\n\n- 제1장 시작\n  - 1.첫 절\n\n# 제1장 시작")
    txt = bl.to_text(body, entries)
    assert txt.startswith("차례\n\n제1장 시작\n  1.첫 절\n\n제1장 시작\n\n1.첫 절\n\n본문 문장이다.")
    assert "#" in txt.split("\n")[-1]          # 본문의 '#태그' 는 그대로
    assert bl.to_markdown("본문", []) == "본문" and bl.to_text("본문", []) == "본문"


def test_refine_pdf_pages_end_to_end():
    pages = [_body_page(0), _toc_page()]
    for i in range(2, 40):
        extra = []
        if i == 29:
            extra = [_line("1.경영은 기업경영이다", 140, 300, 800, 60)]
        pages.append(_body_page(i, f"제1장새로운경영패러다임{i}" if i % 2 else str(i), top_y=300, extra=extra))
    md, txt, notes = bl.refine_pdf_pages(pages)
    assert md.startswith("# 차례\n\n- 역자의말")
    # 장 표제지가 없어 못 찾은 장 제목은 첫 절 제목 앞에 넣는다 (정제가 '1.경영'→'1. 경영' 띄움)
    assert "\n# 제1장 새로운 경영 패러다임\n\n## 1. 경영은 기업경영이다\n\n" in md
    assert "패러다임2" not in md and "패러다임3" not in md        # 꼬리말 '제1장새로운경영패러다임N'
    assert "#" not in txt and "\n제1장 새로운 경영 패러다임\n\n1. 경영은 기업경영이다\n\n" in txt
    assert notes[0] == "여백의 쪽번호·머리말·꼬리말 38줄 제거"
    assert notes[1].startswith("차례 9항목 중 1개를 본문 제목으로 정비(+못 찾은 장 제목 1개")


def test_refine_pdf_pages_without_toc_reports():
    md, txt, notes = bl.refine_pdf_pages([_body_page(i) for i in range(5)])
    assert md == txt and "차례를 찾지 못해" in notes[0]


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ALL {len(tests)} TESTS PASSED SUCCESSFULLY!")
