"""
폴더 이미지 표 일괄 추출 CLI.

폴더 안의 모든 이미지에서 표를 인식해 하나의 CSV/Excel로 합친다.

사용 예:
    python batch_folder.py "C:\\경로\\이미지폴더"
    python batch_folder.py 폴더 --lang korean --recursive --out 결과
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser(description="폴더 이미지 표 일괄 추출 → CSV/Excel")
    ap.add_argument("folder", help="이미지가 들어있는 폴더 경로")
    ap.add_argument("--out", default=None, help="출력 파일 접두어 (기본: 폴더명_통합)")
    ap.add_argument("--lang", default="korean",
                    choices=["korean", "en", "ch", "japan"], help="인식 언어")
    ap.add_argument("--recursive", action="store_true", help="하위 폴더까지 탐색")
    ap.add_argument("--no-dedup-header", action="store_true",
                    help="반복 헤더 자동 제거 끄기")
    args = ap.parse_args()

    import batch
    import exporters
    from table_engine import TableEngine

    if not os.path.isdir(args.folder):
        print(f"[오류] 폴더가 아닙니다: {args.folder}")
        sys.exit(1)

    files = batch.discover_images(args.folder, args.recursive)
    if not files:
        print("[오류] 폴더에 이미지 파일이 없습니다. (png, jpg, bmp, tiff, webp)")
        sys.exit(1)

    print(f">> {len(files)}개 이미지 처리 시작 (언어: {args.lang})")
    print(">> 최초 실행 시 표 인식 모델 로딩에 시간이 걸립니다...\n")

    engine = TableEngine(lang=args.lang)
    per_table: list[tuple[str, list[list[str]]]] = []
    errors: list[tuple[str, str]] = []

    for i, path in enumerate(files, 1):
        name = os.path.splitext(os.path.basename(path))[0]
        print(f"[{i}/{len(files)}] {os.path.basename(path)}", flush=True)
        try:
            tables = engine.recognize_image_file(path)
        except Exception as exc:  # noqa: BLE001
            errors.append((name, str(exc)))
            print(f"    (오류: {exc})")
            continue
        if not tables:
            print("    (표 없음)")
        elif len(tables) == 1:
            per_table.append((name, tables[0]))
            print(f"    표 1개 ({len(tables[0])}행)")
        else:
            for ti, grid in enumerate(tables, 1):
                per_table.append((f"{name}-표{ti}", grid))
            print(f"    표 {len(tables)}개")

    if not per_table:
        print("\n[결과] 표를 찾지 못했습니다.")
        sys.exit(1)

    combined = batch.combine_tables(per_table, dedup_header=not args.no_dedup_header)

    if args.out:
        out_prefix = args.out
    else:
        folder_name = os.path.basename(os.path.normpath(args.folder))
        out_prefix = os.path.join(args.folder, f"{folder_name}_통합")

    csv_path = out_prefix + ".csv"
    xlsx_path = out_prefix + ".xlsx"
    exporters.save_grid_to_csv(combined, csv_path)
    exporters.save_grids_to_xlsx([("통합", combined)] + per_table, xlsx_path)

    print(f"\n[완료] 파일 {len(files)}개 → 표 {len(per_table)}개, 통합 {max(0, len(combined) - 1)}행")
    if errors:
        print(f"        오류 {len(errors)}건")
    print(f"  CSV  : {csv_path}")
    print(f"  Excel: {xlsx_path}")


if __name__ == "__main__":
    main()
