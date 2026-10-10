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
