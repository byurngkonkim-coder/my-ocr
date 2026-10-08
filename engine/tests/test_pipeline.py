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


def test_paths_anchoring():
    res_dir = _ROOT / "결과"
    assert res_dir.is_dir()
    samples_dir = _ROOT / "samples"
    assert samples_dir.is_dir()


if __name__ == "__main__":
    test_imports()
    test_grid_to_tsv()
    test_combine_tables()
    test_korean_corrector()
    test_paths_anchoring()
    print("ALL 5 TESTS PASSED SUCCESSFULLY!")
