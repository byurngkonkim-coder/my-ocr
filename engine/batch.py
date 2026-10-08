"""
폴더 일괄 처리 로직 - 폴더 안의 여러 이미지에서 표를 인식해
하나의 데이터셋으로 합친다. (여러 이미지에서 데이터를 긁어모으는 효과)

- discover_images: 폴더에서 이미지 파일을 자연 정렬 순으로 수집
- combine_tables: 여러 표를 '출처파일' 열을 붙여 세로로 이어붙임(중복 헤더 자동 제거)
"""
from __future__ import annotations

import os
import re

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
SOURCE_COL = "출처파일"


def _natural_key(name: str):
    """'img2' < 'img10' 처럼 사람이 기대하는 순서로 정렬하기 위한 키."""
    return [int(tok) if tok.isdigit() else tok.lower()
            for tok in re.split(r"(\d+)", name)]


def discover_images(folder: str, recursive: bool = False) -> list[str]:
    """폴더에서 이미지 파일 경로 목록을 자연 정렬 순으로 반환."""
    paths: list[str] = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                if os.path.splitext(f)[1].lower() in IMAGE_EXTS:
                    paths.append(os.path.join(root, f))
    else:
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            if os.path.isfile(p) and os.path.splitext(f)[1].lower() in IMAGE_EXTS:
                paths.append(p)
    paths.sort(key=lambda p: _natural_key(os.path.basename(p)))
    return paths


def _rows_equal(a: list[str], b: list[str]) -> bool:
    if b is None or len(a) != len(b):
        return False
    return [c.strip() for c in a] == [c.strip() for c in b]


def combine_tables(items: list[tuple[str, list[list[str]]]],
                   dedup_header: bool = True) -> list[list[str]]:
    """여러 표를 하나의 격자로 합친다.

    items: (출처이름, 격자) 리스트
    - 맨 앞에 '출처파일' 열을 추가해 각 행이 어느 이미지에서 왔는지 표시
    - 첫 표의 첫 행을 대표 헤더로 삼고, 이후 표의 동일한 헤더 행은 자동 제거
    반환: 합쳐진 2차원 격자 (첫 행은 헤더)
    """
    combined: list[list[str]] = []
    master_header: list[str] | None = None

    for source, grid in items:
        if not grid:
            continue
        for r, row in enumerate(grid):
            row = list(row)
            if master_header is None and r == 0:
                master_header = row
                combined.append([SOURCE_COL] + row)   # 헤더 줄
                continue
            if dedup_header and r == 0 and _rows_equal(row, master_header):
                continue  # 반복되는 헤더는 건너뜀
            combined.append([source] + row)

    # 열 개수를 최대값으로 맞춤(빈칸 채움)
    max_cols = max((len(r) for r in combined), default=0)
    for row in combined:
        row.extend([""] * (max_cols - len(row)))
    return combined


def split_source_tables(items: list[tuple[str, list[list[str]]]]):
    """엑셀 시트용: (통합 라벨 제외한) 개별 표 목록을 그대로 돌려준다."""
    return list(items)
