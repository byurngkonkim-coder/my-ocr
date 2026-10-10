import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ENGINE = _HERE.parent
_ROOT = _ENGINE.parent

if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

import exporters
import batch
import korean_corrector
import ocr_engine
import table_engine
import uth_fixed_engine


def test_imports():
    assert exporters is not None
    assert batch is not None
    assert korean_corrector is not None
    assert ocr_engine is not None
    assert table_engine is not None
    assert uth_fixed_engine is not None


def test_grid_to_tsv():
    grid = [["이름", "부서"], ["홍길동", "기획팀"]]
    tsv = exporters.grid_to_tsv(grid)
    assert tsv == "이름\t부서\n홍길동\t기획팀"


def test_combine_tables():
    t1 = ("파일1", [["헤더A", "헤더B"], ["1", "2"]])
    t2 = ("파일2", [["헤더A", "헤더B"], ["3", "4"]])
    combined = batch.combine_tables([t1, t2], dedup_header=True)
    assert len(combined) == 3
    assert combined[0] == ["출처파일", "헤더A", "헤더B"]
    assert combined[1] == ["파일1", "1", "2"]
    assert combined[2] == ["파일2", "3", "4"]


def test_korean_corrector():
    text = "테스트 문장입니다."
    refined = korean_corrector.refine_korean_text(text)
    assert isinstance(refined, str)
    assert len(refined) > 0


def test_korean_corrector_preserves():
    # 교정기가 망가뜨리면 안 되는 입력 (2026-10-10 품질검사에서 실측된 오변환)
    keep = [
        "확인해 주세요", "결재를 승인해 주십시오", "총 66 명이 참석", "Route 66 Highway",
        "1999 년 설립", "가격 1,099.", "수량99", "저장되었습니다", "이용자관리화면",
        "무등록 업체", "무등산", "넙적다리", "거대해서", "변수없이", "말이 통한다고",
        "공부하는구나",
        # 정제본 23권(13MB) 실측에서 교정기가 망가뜨리던 정상 문장 (2026-10-10)
        "목표를 달성하기 위해서는 노력해야 한다.",
        "조직은 변화에 대비하고 있어야 한다.",
        "경영자는 중요한 역할을 한다.",
        "마이클 포터(Michael Porter)의 지적(知的) 기여",
        "경영자들에게 필요한 것",
        "마이크로소프트는 알렉산드로스의 전략을 배웠다",
        "우리나라에서 사람들에게는",
        "그의 말을 들은 뒤 참고할 자료",
        "Z이론의 핵심",
        "머지않아 마지못해 못지않은",
        "3가지를 꼽는다. 500이라는 숫자",
        "어떻게 형성되는가에 달려 있다",
        "GE에서는 GM에게만 A은행에",
        "조직이 비대해지면 실수없이 별수없이",
        "나는 안 가. 기로에 서 있다",
        "문제를 드러나게 하되 6,000도이므로 20만까지",
        "요시하루와 T. 의 관계",
        "첫째는 도, 둘째는 천이다",
        "전해 들은 이야기. 할 때 이미 늦었다",
        "잘 대해주지만 마이크로프로세서에만 스탠퍼드대학교로부터",
    ]
    for text in keep:
        assert korean_corrector.refine_korean_text(text) == text, text


def test_korean_corrector_fixes():
    cases = {
        '66안녕하세요.99': '"안녕하세요."',
        "할수있다": "할 수 있다",
        "이를위해": "이를 위해",
        "그로인해": "그로 인해",
        "먹을것이다": "먹을 것이다",
        "않되는": "안 되는",
        "훌륨한": "훌륭한",
    }
    for src, want in cases.items():
        assert korean_corrector.refine_korean_text(src) == want, (src, korean_corrector.refine_korean_text(src))


def test_unique_path():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "결과.xlsx")
        assert exporters.unique_path(p) == p
        open(p, "w").close()
        assert exporters.unique_path(p) == os.path.join(d, "결과_2.xlsx")


def test_rapidocr_params_per_language():
    # 영어·일본어를 골라도 기본(중국어) 모델로 인식하던 문제 (2026-10-10)
    assert ocr_engine.rapidocr_params("korean")["Rec.lang_type"].name == "KOREAN"
    assert ocr_engine.rapidocr_params("en")["Rec.lang_type"].name == "EN"
    assert ocr_engine.rapidocr_params("japan")["Rec.lang_type"].name == "JAPAN"
    assert ocr_engine.rapidocr_params("ch") is None


def test_korean_refine_only_for_korean():
    lines = [("Route 66 Highway", 1.0)]
    assert ocr_engine.OCREngine("en")._refine_lines(lines) == lines
    assert ocr_engine.OCREngine("korean")._refine_lines([("확인해 주세요", 1.0)]) == [("확인해 주세요", 1.0)]


def test_paddle_fallback_error_names_real_cause():
    err = ocr_engine.paddle_fallback_error(OSError("모델 파일 손상"))
    assert "RapidOCR" in str(err) and "모델 파일 손상" in str(err)


def test_paths_anchoring():
    # 결과/ 는 .gitignore 대상이라 새로 받은 PC에는 없다 — 커밋되는 samples/ 만 확인
    samples_dir = _ROOT / "samples"
    assert samples_dir.is_dir()


if __name__ == "__main__":
    tests = [test_imports, test_grid_to_tsv, test_combine_tables, test_korean_corrector,
             test_korean_corrector_preserves, test_korean_corrector_fixes, test_unique_path,
             test_rapidocr_params_per_language, test_korean_refine_only_for_korean,
             test_paddle_fallback_error_names_real_cause, test_paths_anchoring]
    for t in tests:
        t()
    print(f"ALL {len(tests)} TESTS PASSED SUCCESSFULLY!")
