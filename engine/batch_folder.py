"""
폴더 일괄 추출 CLI.

- 이미지 폴더: 모든 이미지에서 표를 인식해 하나의 CSV/Excel로 합친다.
- PDF 폴더(--text, 또는 이미지 없이 PDF 만 있으면 자동): PDF 마다 일반 텍스트 TXT + MD 를 결과\\ 에 저장.

사용 예:
    python batch_folder.py "C:\\경로\\이미지폴더"
    python batch_folder.py 폴더 --lang korean --recursive --out 결과
    python batch_folder.py "C:\\경로\\PDF폴더" --text --drop-matter
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser(description="폴더 이미지 표 일괄 추출 → CSV/Excel")
    ap.add_argument("folder", help="이미지가 들어있는 폴더 경로")
    ap.add_argument("--out", default=None, help="출력 파일 접두어 (기본: 폴더명_통합). 텍스트 모드는 저장 폴더 (기본: 결과\\)")
    ap.add_argument("--lang", default="korean",
                    choices=["korean", "en", "ch", "japan"], help="인식 언어")
    ap.add_argument("--recursive", action="store_true", help="하위 폴더까지 탐색")
    ap.add_argument("--no-dedup-header", action="store_true",
                    help="반복 헤더 자동 제거 끄기")
    ap.add_argument("--text", action="store_true",
                    help="PDF 일반 텍스트 일괄 추출 (이미지 없이 PDF 만 있는 폴더는 자동으로 이 모드)")
    ap.add_argument("--no-refine", action="store_true", help="텍스트 모드: 문장 정제 끄기 (쪽별 원문 TXT)")
    ap.add_argument("--drop-matter", action="store_true", help="텍스트 모드: 부속물(판권·광고 등) 삭제")
    args = ap.parse_args()

    import batch
    import exporters
    from table_engine import TableEngine

    if not os.path.isdir(args.folder):
        print(f"[오류] 폴더가 아닙니다: {args.folder}")
        sys.exit(1)

    files = batch.discover_images(args.folder, args.recursive)
    if args.text or (not files and batch.discover_pdfs(args.folder, args.recursive)):
        run_text(args)
        return
    if not files:
        print("[오류] 폴더에 이미지 파일이 없습니다. (png, jpg, bmp, tiff, webp — PDF 는 --text)")
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

    # 이전 결과는 덮어쓰지 않는다 (같은 이름이 있으면 _2, _3 …)
    csv_path = exporters.unique_path(out_prefix + ".csv")
    xlsx_path = exporters.unique_path(out_prefix + ".xlsx")
    exporters.save_grid_to_csv(combined, csv_path)
    exporters.save_grids_to_xlsx([("통합", combined)] + per_table, xlsx_path)

    print(f"\n[완료] 파일 {len(files)}개 → 표 {len(per_table)}개, 통합 {max(0, len(combined) - 1)}행")
    if errors:
        print(f"        오류 {len(errors)}건")
    print(f"  CSV  : {csv_path}")
    print(f"  Excel: {xlsx_path}")


def run_text(args):
    """PDF 마다 일반 텍스트 추출 → 이름_텍스트.txt (+ 문장 정제 시 .md: 여백 제거 → 차례 기준 장·절 정비 → 정제).
    저장 위치: --out 폴더, 없으면 GUI 와 같은 프로젝트 루트의 결과\\ 폴더."""
    import batch
    import book_layout
    import exporters
    from ocr_engine import OCREngine

    files = batch.discover_pdfs(args.folder, args.recursive)
    if not files:
        print("[오류] 폴더에 PDF 파일이 없습니다.")
        sys.exit(1)
    out_dir = args.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "결과")
    os.makedirs(out_dir, exist_ok=True)
    refine = not args.no_refine
    print(f">> PDF {len(files)}개 텍스트 추출 시작 (언어: {args.lang}, 문장 정제: {'켬' if refine else '끔'})")
    print(f">> 저장 위치: {out_dir}\n")
    engine = OCREngine(lang=args.lang)
    errors = 0
    for i, path in enumerate(files, 1):
        name = os.path.splitext(os.path.basename(path))[0]
        print(f"[{i}/{len(files)}] {os.path.basename(path)}", flush=True)
        prog = lambda cur, total: print(f"\r    {cur}/{total} 쪽", end="", flush=True)
        try:
            if refine:
                md, text, notes = book_layout.pdf_to_text(engine, path, args.drop_matter, prog)
            else:
                pages = engine.ocr_pdf(path, progress_cb=prog)
                md, notes = None, []
                text = "\n".join(f"===== {n} 페이지 =====\n" + "\n".join(t for t, _ in lines) + "\n"
                                 for n, lines in enumerate(pages, 1)).strip()
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"\n    (오류: {exc})")
            continue
        saved = exporters.save_texts(os.path.join(out_dir, f"{name}_텍스트"), text, md)
        print("\n    " + " / ".join(notes) if notes else "")
        print("    저장: " + ", ".join(os.path.basename(p) for p in saved))
    print(f"\n[완료] PDF {len(files)}개 중 {len(files) - errors}개 저장" + (f", 오류 {errors}건" if errors else ""))


if __name__ == "__main__":
    main()
