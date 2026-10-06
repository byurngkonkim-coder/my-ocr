"""표 격자(2차원 리스트)를 엑셀/CSV/TSV로 내보내는 헬퍼."""
from __future__ import annotations

import csv


def grid_to_tsv(grid: list[list[str]]) -> str:
    """격자 -> 탭 구분 문자열 (엑셀/시트에 바로 붙여넣기 가능)."""
    return "\n".join("\t".join(cell.replace("\t", " ") for cell in row) for row in grid)


def save_grid_to_csv(grid: list[list[str]], path: str) -> None:
    """격자 -> CSV 파일. Excel에서 한글이 깨지지 않도록 UTF-8 BOM 사용."""
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerows(grid)


def save_grids_to_xlsx(tables: list[tuple[str, list[list[str]]]], path: str) -> None:
    """여러 표를 하나의 엑셀 파일로 저장한다. (표 하나당 시트 하나)

    tables: (시트이름, 격자) 튜플의 리스트
    """
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)  # 기본 빈 시트 제거

    used_names: set[str] = set()
    for label, grid in tables:
        sheet_name = _safe_sheet_name(label, used_names)
        used_names.add(sheet_name)
        ws = wb.create_sheet(title=sheet_name)
        for row in grid:
            ws.append(list(row))
        _auto_width(ws, grid, get_column_letter)

    if not wb.sheetnames:  # 표가 하나도 없을 때 방지
        wb.create_sheet(title="빈표")
    wb.save(path)


def _safe_sheet_name(name: str, used: set[str]) -> str:
    # 엑셀 시트명 제약: 31자 이하, : \ / ? * [ ] 금지, 중복 불가
    for ch in r':\/?*[]':
        name = name.replace(ch, "_")
    name = (name or "표").strip()[:31] or "표"
    base = name
    i = 2
    while name in used:
        suffix = f"_{i}"
        name = base[: 31 - len(suffix)] + suffix
        i += 1
    return name


def _auto_width(ws, grid, get_column_letter, max_width: int = 60) -> None:
    if not grid:
        return
    cols = max(len(row) for row in grid)
    for c in range(cols):
        longest = 0
        for row in grid:
            if c < len(row):
                # 한글은 폭이 넓으므로 약 1.7배로 근사
                length = sum(2 if ord(ch) > 0x2000 else 1 for ch in str(row[c]))
                longest = max(longest, length)
        ws.column_dimensions[get_column_letter(c + 1)].width = min(max_width, max(8, longest + 2))
