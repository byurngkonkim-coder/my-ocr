"""
표 OCR - 캡처된 표에서 문자를 정확히 추출해 표 형식으로 내보내는 GUI 프로그램.

- 표 추출 모드: 표 구조(행·열·셀)를 인식해 격자로 복원 -> 엑셀/CSV/TSV 내보내기
- 일반 텍스트 모드: 일반 이미지/문서에서 줄글 텍스트 추출
- 이미지와 PDF 모두 지원, 한글/영문/중문/일문 선택
- 인식은 백그라운드 스레드에서 실행되어 화면이 멈추지 않는다.
"""
from __future__ import annotations

import os
import sys
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from PIL import Image, ImageTk

# 드래그앤드롭 (없으면 버튼으로만 열기 — 다른 PC에서 install.bat 재실행 전에도 실행되도록)
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
except ImportError:
    TkinterDnD = None

from table_engine import TableEngine
from ocr_engine import OCREngine
from uth_fixed_engine import UTHFixedEngine
import exporters
import text_refiner

# 화면 표시명 -> PaddleOCR 언어 코드
LANG_OPTIONS = {
    "한국어 (한글+영문)": "korean",
    "영어": "en",
    "중국어(간체)": "ch",
    "일본어": "japan",
}

PREVIEW_MAX = (460, 640)
RESULT_DIR = os.path.join(os.path.dirname(_HERE), "결과")   # 인식 결과 자동 저장 위치 (프로젝트 루트/결과)


class TableOCRApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("표 OCR - 캡처 표 문자추출")
        # 창 크기를 화면 배율에 맞춘다 — main()이 DPI 인식을 켜서 글자·버튼은 배율만큼 커지는데
        # 창 크기를 픽셀 고정값으로 두면 고배율(200% 이상) 화면에서 툴바가 잘린다
        s = max(1.0, float(root.tk.call("tk", "scaling")) / (96 / 72))
        w = min(int(1140 * s), root.winfo_screenwidth())
        h = min(int(720 * s), root.winfo_screenheight() - int(80 * s))
        root.geometry(f"{w}x{h}")
        root.minsize(min(int(900 * s), w), min(int(560 * s), h))

        # 엔진 (언어별로 재사용, 언어 바뀌면 재생성)
        self.table_engine: TableEngine | None = None
        self.text_engine: OCREngine | None = None
        self.uth_engine: UTHFixedEngine | None = None

        self.loaded_path: str | None = None
        self.loaded_kind: str | None = None   # "image" | "pdf"
        self.preview_imgtk = None
        self.tables_data: list[tuple[str, list[list[str]]]] = []  # (라벨, 격자)
        self.plain_text: str = ""
        self.notebook: ttk.Notebook | None = None

        self.msg_queue: "queue.Queue" = queue.Queue()

        self._build_ui()
        self._poll_queue()

    # ------------------------------------------------------------------ #
    # UI 구성
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        bar = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        bar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(bar, text="🖼  이미지 열기", command=self.open_image).pack(side=tk.LEFT, padx=3)
        ttk.Button(bar, text="📄  PDF 열기", command=self.open_pdf).pack(side=tk.LEFT, padx=3)
        ttk.Button(bar, text="📁  폴더 일괄", command=self.open_folder_batch).pack(side=tk.LEFT, padx=3)

        ttk.Separator(bar, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=8)

        ttk.Label(bar, text="모드:").pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value="table")
        ttk.Radiobutton(bar, text="표 추출", value="table", variable=self.mode_var,
                        command=self._on_mode_change).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Radiobutton(bar, text="일반 텍스트", value="plain", variable=self.mode_var,
                        command=self._on_mode_change).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Radiobutton(bar, text="사용자관리(고정양식)", value="uth", variable=self.mode_var,
                        command=self._on_mode_change).pack(side=tk.LEFT, padx=(2, 0))

        ttk.Label(bar, text="언어:").pack(side=tk.LEFT, padx=(12, 4))
        self.lang_var = tk.StringVar(value="한국어 (한글+영문)")
        lang_cb = ttk.Combobox(bar, textvariable=self.lang_var,
                               values=list(LANG_OPTIONS.keys()), state="readonly", width=18)
        lang_cb.pack(side=tk.LEFT)
        lang_cb.bind("<<ComboboxSelected>>", self._on_lang_change)

        self.run_btn = ttk.Button(bar, text="▶  인식 실행", command=self.run, state=tk.DISABLED)
        self.run_btn.pack(side=tk.LEFT, padx=(12, 3))

        # 두 번째 줄: 왼쪽 '일반 텍스트' 후처리 옵션, 오른쪽 내보내기 버튼
        # (한 줄에 다 두면 화면 배율이 큰 PC에서 오른쪽 버튼이 창 밖으로 잘린다)
        opts = ttk.Frame(self.root, padding=(8, 0, 8, 4))
        opts.pack(side=tk.TOP, fill=tk.X)

        self.csv_btn = ttk.Button(opts, text="💾 CSV", command=self.export_csv, state=tk.DISABLED)
        self.csv_btn.pack(side=tk.RIGHT, padx=3)
        self.xlsx_btn = ttk.Button(opts, text="💾 Excel", command=self.export_xlsx, state=tk.DISABLED)
        self.xlsx_btn.pack(side=tk.RIGHT, padx=3)
        self.copy_btn = ttk.Button(opts, text="📋 복사", command=self.copy_result, state=tk.DISABLED)
        self.copy_btn.pack(side=tk.RIGHT, padx=3)

        ttk.Label(opts, text="일반 텍스트 후처리:").pack(side=tk.LEFT)
        self.refine_var = tk.BooleanVar(value=True)
        self.matter_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="문장 정제 (쪽번호·머리글 제거, 줄 잇기)",
                        variable=self.refine_var,
                        command=self._on_refine_change).pack(side=tk.LEFT, padx=(6, 0))
        self.matter_chk = ttk.Checkbutton(opts, text="부속물 삭제 (차례·판권·광고)",
                                          variable=self.matter_var)
        self.matter_chk.pack(side=tk.LEFT, padx=(12, 0))

        body = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

        left = ttk.Frame(body)
        hint = "\n\n이미지 또는 PDF를 열어주세요."
        if TkinterDnD is not None:
            hint += "\n\n(이미지·PDF·폴더를 여기로 끌어다 놓아도 됩니다)"
        self.preview_label = ttk.Label(left, text=hint,
                                       anchor="center", relief="solid", padding=6)
        self.preview_label.pack(fill=tk.BOTH, expand=True)
        if TkinterDnD is not None:
            self.preview_label.drop_target_register(DND_FILES)
            self.preview_label.dnd_bind("<<Drop>>", self._on_drop)
        body.add(left, weight=1)

        right = ttk.Frame(body)
        ttk.Label(right, text="인식 결과").pack(anchor="w")
        self.result_container = ttk.Frame(right, relief="solid", borderwidth=1)
        self.result_container.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        body.add(right, weight=3)
        self._render_message("결과가 여기에 표시됩니다.")

        status = ttk.Frame(self.root)
        status.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="준비됨")
        ttk.Label(status, textvariable=self.status_var, anchor="w").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=10, pady=4)
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=190)
        self.progress.pack(side=tk.RIGHT, padx=10, pady=4)

    # ------------------------------------------------------------------ #
    # 파일 열기 / 미리보기
    # ------------------------------------------------------------------ #
    def open_image(self, path=None):
        path = path or filedialog.askopenfilename(
            title="이미지 선택",
            filetypes=[("이미지 파일", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp"),
                       ("모든 파일", "*.*")])
        if not path:
            return
        self.loaded_path, self.loaded_kind = path, "image"
        self._show_image_preview(path)
        self.run_btn.config(state=tk.NORMAL)
        self.status_var.set(f"불러옴: {os.path.basename(path)}")

    def open_pdf(self, path=None):
        path = path or filedialog.askopenfilename(
            title="PDF 선택", filetypes=[("PDF 파일", "*.pdf"), ("모든 파일", "*.*")])
        if not path:
            return
        self.loaded_path, self.loaded_kind = path, "pdf"
        self._show_pdf_preview(path)
        self.run_btn.config(state=tk.NORMAL)

    def _on_drop(self, event):
        """미리보기 영역에 끌어다 놓은 이미지/PDF/폴더를 연다 (여러 개면 첫 항목)."""
        # 파일이 열려 있는데 실행 버튼이 꺼져 있으면 = 인식 중
        if self.loaded_path and self.run_btn.instate(["disabled"]):
            self.status_var.set("인식 중에는 새 파일을 열 수 없습니다.")
            return
        paths = self.root.tk.splitlist(event.data)  # 공백 든 경로는 {..}로 감싸져 온다
        if not paths:
            return
        path = paths[0]
        import batch
        ext = os.path.splitext(path)[1].lower()
        if os.path.isdir(path):
            self.open_folder_batch(path)
        elif ext == ".pdf":
            self.open_pdf(path)
        elif ext in batch.IMAGE_EXTS:
            self.open_image(path)
        else:
            self.status_var.set(f"지원하지 않는 파일입니다: {os.path.basename(path)}")

    def _show_image_preview(self, path):
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail(PREVIEW_MAX)
                self.preview_imgtk = ImageTk.PhotoImage(im)
            self.preview_label.config(image=self.preview_imgtk, text="")
        except Exception as exc:
            self.preview_imgtk = None
            self.preview_label.config(image="", text=f"미리보기 실패:\n{exc}")

    def _show_pdf_preview(self, path):
        try:
            import pymupdf as fitz
            doc = fitz.open(path)
            try:
                page = doc.load_page(0)
                pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4),
                                      colorspace=fitz.csRGB, alpha=False)
                im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                total = doc.page_count
            finally:
                doc.close()
            im.thumbnail(PREVIEW_MAX)
            self.preview_imgtk = ImageTk.PhotoImage(im)
            self.preview_label.config(image=self.preview_imgtk, text="")
            self.status_var.set(f"불러옴: {os.path.basename(path)}  ({total}페이지)")
        except Exception as exc:
            self.preview_imgtk = None
            self.preview_label.config(image="", text=f"PDF 미리보기 실패:\n{exc}")
            self.status_var.set(f"불러옴: {os.path.basename(path)}")

    # ------------------------------------------------------------------ #
    # 모드 / 언어 변경
    # ------------------------------------------------------------------ #
    def _on_mode_change(self):
        labels = {"table": "표 추출", "plain": "일반 텍스트", "uth": "사용자관리(고정양식)"}
        self.status_var.set(
            f"모드: {labels.get(self.mode_var.get(), '표 추출')} (다음 인식부터 적용)")

    def _on_lang_change(self, _event=None):
        self.status_var.set(f"언어: {self.lang_var.get()} (다음 인식부터 적용)")

    def _on_refine_change(self):
        # 부속물 삭제는 문장 정제가 켜져 있을 때만 동작
        self.matter_chk.config(state=tk.NORMAL if self.refine_var.get() else tk.DISABLED)
        self.status_var.set("문장 정제 켬 (다음 인식부터 적용)"
                            if self.refine_var.get() else "문장 정제 끔 — 쪽별 원문 그대로 (다음 인식부터 적용)")

    # ------------------------------------------------------------------ #
    # 인식 실행 (백그라운드 스레드)
    # ------------------------------------------------------------------ #
    def run(self):
        if not self.loaded_path:
            return
        lang = LANG_OPTIONS[self.lang_var.get()]
        if self.loaded_kind == "folder":
            batch_mode = self.mode_var.get() if self.mode_var.get() in ("table", "uth") else "table"
            self._set_busy(True, "폴더 일괄 처리 준비 중...")
            self._set_export_enabled(False)
            threading.Thread(target=self._worker_batch,
                             args=(self.loaded_path, lang, False, batch_mode), daemon=True).start()
            return
        self._set_busy(True, "인식 준비 중...")
        self._set_export_enabled(False)
        mode = self.mode_var.get()
        refine = (self.refine_var.get(), self.refine_var.get() and self.matter_var.get())
        threading.Thread(target=self._worker,
                         args=(self.loaded_path, self.loaded_kind, mode, lang, refine),
                         daemon=True).start()

    def open_folder_batch(self, folder=None):
        folder = folder or filedialog.askdirectory(title="이미지 폴더 선택 (일괄 표 추출)")
        if not folder:
            return
        import batch
        files = batch.discover_images(folder, recursive=False)
        if not files:
            messagebox.showinfo(
                "알림",
                "선택한 폴더에 이미지 파일이 없습니다.\n(png, jpg, jpeg, bmp, tif, tiff, webp)")
            return
        mode = self.mode_var.get() if self.mode_var.get() in ("table", "uth") else "table"
        mode_label = "사용자관리(고정양식)" if mode == "uth" else "표 추출"
        if not messagebox.askyesno(
                "폴더 일괄 처리",
                f"'{os.path.basename(folder)}' 폴더의 이미지 {len(files)}개를 인식해\n"
                f"하나로 합칩니다.\n\n"
                f"· 모드: {mode_label} / 언어: {self.lang_var.get()}\n"
                f"· 파일이 많으면 시간이 걸릴 수 있습니다.\n\n계속할까요?"):
            return
        self.loaded_path = folder
        self.loaded_kind = "folder"
        self.mode_var.set(mode)
        self._show_folder_preview(folder, len(files))
        self.run_btn.config(state=tk.NORMAL)
        self._set_busy(True, "폴더 일괄 처리 준비 중...")
        self._set_export_enabled(False)
        lang = LANG_OPTIONS[self.lang_var.get()]
        threading.Thread(target=self._worker_batch,
                         args=(folder, lang, False, mode), daemon=True).start()

    def _show_folder_preview(self, folder, count):
        self.preview_imgtk = None
        self.preview_label.config(
            image="",
            text=f"\n\n📁 폴더 일괄 처리\n\n{os.path.basename(folder)}\n\n이미지 {count}개")

    def _worker_batch(self, folder, lang, recursive, mode="table"):
        try:
            import batch
            self._prepare_engine(mode, lang)
            engine = self.uth_engine if mode == "uth" else self.table_engine
            files = batch.discover_images(folder, recursive)
            total = len(files)
            per_table: list[tuple[str, list[list[str]]]] = []
            errors: list[str] = []
            for idx, path in enumerate(files, 1):
                name = os.path.splitext(os.path.basename(path))[0]
                self.msg_queue.put(("status",
                    f"[{idx}/{total}] 표 인식 중: {os.path.basename(path)}"))
                try:
                    tables = engine.recognize_image_file(path)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{os.path.basename(path)}: {type(exc).__name__}: {exc}")
                    continue
                if len(tables) == 1:
                    per_table.append((name, tables[0]))
                elif len(tables) > 1:
                    for ti, grid in enumerate(tables, 1):
                        per_table.append((f"{name}-표{ti}", grid))

            if not per_table:
                self.msg_queue.put(("message",
                    f"{total}개 파일에서 표를 찾지 못했습니다.\n\n"
                    "표 테두리가 뚜렷한 이미지인지 확인해 보세요."))
                self.msg_queue.put(("status", "완료 - 표 0개" + self._error_note(errors)))
                return

            combined = batch.combine_tables(per_table)
            ui_tables = [("통합", combined)] + per_table
            self.msg_queue.put(("tables", ui_tables))
            summary = (f"완료 - 파일 {total}개, 표 {len(per_table)}개, "
                       f"통합 {max(0, len(combined) - 1)}행")
            summary += self._error_note(errors)
            hidden = max(0, len(ui_tables) - 40)
            if hidden:
                summary += f" (탭 {hidden}개 생략, 저장 시 모두 포함)"
            summary += self._size_note(mode, files)
            summary += self._auto_save(f"{os.path.basename(os.path.normpath(folder))}_통합", ui_tables)
            self.msg_queue.put(("status", summary))
        except Exception as exc:  # noqa: BLE001
            self.msg_queue.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            self.msg_queue.put(("done", None))

    def _worker(self, path, kind, mode, lang, refine=(False, False)):
        try:
            self._prepare_engine(mode, lang)
            base = os.path.splitext(os.path.basename(path))[0]
            if mode in ("table", "uth"):
                results = self._worker_table(path, kind, mode)
                if results:
                    self.msg_queue.put(("status", f"완료 - 표 {len(results)}개"
                                        + self._size_note(mode, [path])
                                        + self._auto_save(f"{base}_표", results)))
                else:
                    self.msg_queue.put(("status", "완료 - 표 0개" + self._size_note(mode, [path])))
            else:
                text, notes = self._worker_plain(path, kind, *refine)
                saved = self._auto_save(f"{base}_텍스트", text) if text else ""
                self.msg_queue.put(("status", "완료" + saved +
                                    (" — 삭제: " + " / ".join(notes) if notes else "")))
        except Exception as exc:  # noqa: BLE001
            self.msg_queue.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            self.msg_queue.put(("done", None))

    def _prepare_engine(self, mode, lang):
        # 엔진마다 자기 언어를 비교한다 (공용 변수 하나로 비교하면 다른 모드의 낡은 엔진이 재사용됨)
        if mode == "table":
            if self.table_engine is None or self.table_engine.lang != lang:
                self.msg_queue.put(("status",
                    "표 인식 모델 로딩 중... (최초 실행 시 모델 다운로드로 시간이 걸립니다)"))
                eng = TableEngine(lang=lang)
                eng.ensure_engine()
                self.table_engine = eng
        elif mode == "uth":
            if self.uth_engine is None or self.uth_engine.lang != lang:
                self.msg_queue.put(("status",
                    "OCR 모델 로딩 중... (최초 실행 시 모델 다운로드로 시간이 걸립니다)"))
                eng = UTHFixedEngine(lang=lang)
                eng.ensure_engine()
                self.uth_engine = eng
        else:
            if self.text_engine is None or self.text_engine.lang != lang:
                self.msg_queue.put(("status",
                    "OCR 모델 로딩 중... (최초 실행 시 모델 다운로드로 시간이 걸립니다)"))
                eng = OCREngine(lang=lang)
                eng.ensure_engine()
                self.text_engine = eng

    def _worker_table(self, path, kind, mode="table"):
        engine = self.uth_engine if mode == "uth" else self.table_engine
        results: list[tuple[str, list[list[str]]]] = []
        if kind == "pdf":
            if mode == "uth":
                self.msg_queue.put(("message",
                    "'사용자관리(고정양식)' 모드는 캡처 이미지 전용입니다.\n"
                    "PDF는 '표 추출' 모드를 사용하세요."))
                return
            def prog(cur, total):
                self.msg_queue.put(("status", f"표 인식 중...  {cur}/{total} 페이지"))
            pages = engine.recognize_pdf(path, progress_cb=prog)
            for page_no, tables in pages:
                for ti, grid in enumerate(tables, 1):
                    results.append((f"{page_no}p-표{ti}", grid))
        else:
            self.msg_queue.put(("status", "인식 중..."))
            tables = engine.recognize_image_file(path)
            base = "사용자목록" if mode == "uth" else "표"
            for ti, grid in enumerate(tables, 1):
                results.append((base if len(tables) == 1 else f"{base} {ti}", grid))

        if results:
            self.msg_queue.put(("tables", results))
            return results
        elif mode == "uth":
            self.msg_queue.put(("message",
                "행을 찾지 못했습니다.\n\n· UtradeHub '사용자관리' 캡처(1788x892)인지 확인하세요.\n"
                "· 다른 화면/해상도라면 '표 추출' 모드를 사용하세요."))
        else:
            self.msg_queue.put(("message",
                "표를 찾지 못했습니다.\n\n· 표 테두리가 뚜렷한 이미지를 사용해 보세요.\n"
                "· '일반 텍스트' 모드로도 시도해 볼 수 있습니다."))

    def _worker_plain(self, path, kind, refine=False, drop_matter=False):
        """일반 텍스트 인식. (결과 텍스트, 부속물 삭제 보고 줄 목록)을 돌려준다."""
        if kind == "pdf":
            def prog(cur, total):
                self.msg_queue.put(("status", f"OCR 진행 중...  {cur}/{total} 페이지"))
            pages = self.text_engine.ocr_pdf(path, progress_cb=prog)
        else:
            self.msg_queue.put(("status", "OCR 진행 중..."))
            pages = [self.text_engine.ocr_image_file(path)]
        page_texts = ["\n".join(t for t, _ in lines) for lines in pages]

        notes: list[str] = []
        if refine:
            text, notes = text_refiner.refine_pages(page_texts, drop_matter=drop_matter)
        elif kind == "pdf":
            chunks = []
            for idx, pt in enumerate(page_texts, 1):
                chunks.append(f"===== {idx} 페이지 =====")
                chunks.append(pt)
                chunks.append("")
            text = "\n".join(chunks).strip()
        else:
            text = page_texts[0]
        self.msg_queue.put(("plain", text or "(인식된 텍스트가 없습니다.)"))
        return text, notes

    @staticmethod
    def _error_note(errors):
        """일괄 처리 오류 — 개수만 보이면 원인을 알 수 없어 첫 오류 내용을 붙인다."""
        if not errors:
            return ""
        return f"   ·   ⚠ 오류 {len(errors)}건 (첫 오류 — {errors[0][:120]})"

    def _size_note(self, mode, paths):
        """고정양식 모드에 1788×892 가 아닌 캡처가 들어오면 알린다 (좌표가 어긋나 열이 섞일 수 있음)."""
        if mode != "uth" or self.uth_engine is None:
            return ""
        bad = [p for p in paths if not self.uth_engine.is_expected_size(p)]
        if not bad:
            return ""
        return (f"   ·   ⚠ 1788×892 가 아닌 캡처 {len(bad)}개({os.path.basename(bad[0])} 등) "
                "— 열이 어긋날 수 있으니 '검토필요' 열을 확인하세요")

    def _auto_save(self, base, data):
        """인식 결과를 결과/ 폴더에 자동 저장. 표 목록 → .xlsx, 문자열 → .txt.
        상태줄에 붙일 문구를 돌려준다. 저장 실패는 인식 결과를 막지 않도록 문구로만 알린다."""
        ext = "txt" if isinstance(data, str) else "xlsx"
        try:
            os.makedirs(RESULT_DIR, exist_ok=True)
            path = exporters.unique_path(os.path.join(RESULT_DIR, f"{base}.{ext}"))  # 이전 결과는 덮어쓰지 않는다
            if ext == "txt":
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(data + "\n")
            else:
                exporters.save_grids_to_xlsx(data, path)
        except Exception as exc:  # noqa: BLE001
            return f"   ·   ⚠ 자동 저장 실패: {exc}"
        return f"   ·   결과 저장: 결과\\{os.path.basename(path)}"

    # ------------------------------------------------------------------ #
    # 결과 렌더링
    # ------------------------------------------------------------------ #
    def _clear_results(self):
        for child in self.result_container.winfo_children():
            child.destroy()
        self.notebook = None

    def _render_message(self, msg):
        self._clear_results()
        ttk.Label(self.result_container, text=msg, anchor="center",
                  justify="center", padding=20).pack(fill=tk.BOTH, expand=True)

    def _render_plain(self, text):
        self._clear_results()
        box = scrolledtext.ScrolledText(self.result_container, wrap=tk.WORD,
                                        font=("Malgun Gothic", 11), undo=True)
        box.pack(fill=tk.BOTH, expand=True)
        box.insert(tk.END, text)
        self.plain_text = text

    def _render_tables(self, tables, max_tabs=40):
        self._clear_results()
        self.tables_data = tables            # 내보내기는 전체 사용
        self.notebook = ttk.Notebook(self.result_container)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        for label, grid in tables[:max_tabs]:  # 화면 탭은 최대 max_tabs개만
            tab = self._build_table_view(self.notebook, grid)
            self.notebook.add(tab, text=label)

    @staticmethod
    def _build_table_view(parent, grid):
        frame = ttk.Frame(parent)
        ncols = max((len(r) for r in grid), default=1)
        col_ids = [f"c{i}" for i in range(ncols)]
        tree = ttk.Treeview(frame, columns=col_ids, show="headings", height=14)
        for i, cid in enumerate(col_ids):
            tree.heading(cid, text=f"열 {i + 1}")
            tree.column(cid, width=150, anchor="w", stretch=True)
        for row in grid:
            vals = list(row) + [""] * (ncols - len(row))
            tree.insert("", "end", values=vals)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return frame

    # ------------------------------------------------------------------ #
    # 큐 폴링
    # ------------------------------------------------------------------ #
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "status":
                    self.status_var.set(payload)
                elif kind == "tables":
                    self._render_tables(payload)
                    self._set_export_enabled(True)
                    n = len(payload)
                    self.status_var.set(f"완료 - 표 {n}개 인식됨")
                elif kind == "plain":
                    self._render_plain(payload)
                    self.copy_btn.config(state=tk.NORMAL)
                elif kind == "message":
                    self._render_message(payload)
                elif kind == "error":
                    messagebox.showerror("오류", payload)
                    self.status_var.set("오류 발생")
                elif kind == "done":
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _set_busy(self, busy, status=None):
        if busy:
            self.run_btn.config(state=tk.DISABLED)
            self.progress.start(12)
        else:
            self.run_btn.config(state=tk.NORMAL if self.loaded_path else tk.DISABLED)
            self.progress.stop()
        if status is not None:
            self.status_var.set(status)

    def _set_export_enabled(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        self.copy_btn.config(state=state)
        self.xlsx_btn.config(state=state)
        self.csv_btn.config(state=state)

    # ------------------------------------------------------------------ #
    # 내보내기
    # ------------------------------------------------------------------ #
    def _current_table(self):
        """현재 선택된 탭의 (라벨, 격자) 반환."""
        if not self.tables_data:
            return None
        idx = 0
        if self.notebook is not None:
            try:
                idx = self.notebook.index(self.notebook.select())
            except Exception:
                idx = 0
        return self.tables_data[idx]

    def copy_result(self):
        if self.mode_var.get() == "plain" or not self.tables_data:
            txt = self.plain_text.strip()
            if not txt:
                self.status_var.set("복사할 내용이 없습니다.")
                return
            self._to_clipboard(txt)
            self.status_var.set("텍스트를 클립보드에 복사했습니다.")
            return
        item = self._current_table()
        if not item:
            return
        self._to_clipboard(exporters.grid_to_tsv(item[1]))
        self.status_var.set(f"'{item[0]}'을(를) 표 형식(TSV)으로 복사했습니다. 엑셀에 붙여넣기 하세요.")

    def _default_dir(self):
        root_dir = os.path.abspath(os.path.join(_HERE, ".."))
        if os.path.isdir(RESULT_DIR):
            return RESULT_DIR
        if self.loaded_path and os.path.isfile(self.loaded_path):
            return os.path.dirname(self.loaded_path)
        return root_dir

    def export_xlsx(self):
        if not self.tables_data:
            messagebox.showinfo("알림", "먼저 표 추출을 실행하세요.")
            return
        path = filedialog.asksaveasfilename(
            title="엑셀로 저장", defaultextension=".xlsx",
            initialdir=self._default_dir(),
            initialfile=self._default_name("xlsx"),
            filetypes=[("Excel 통합 문서", "*.xlsx")])
        if not path:
            return
        try:
            exporters.save_grids_to_xlsx(self.tables_data, path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("오류", f"엑셀 저장 실패:\n{exc}")
            return
        self.status_var.set(f"엑셀 저장 완료 (표 {len(self.tables_data)}개): {path}")

    def export_csv(self):
        item = self._current_table()
        if not item:
            messagebox.showinfo("알림", "먼저 표 추출을 실행하세요.")
            return
        path = filedialog.asksaveasfilename(
            title="CSV로 저장 (현재 표)", defaultextension=".csv",
            initialdir=self._default_dir(),
            initialfile=self._default_name("csv"),
            filetypes=[("CSV 파일", "*.csv")])
        if not path:
            return
        try:
            exporters.save_grid_to_csv(item[1], path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("오류", f"CSV 저장 실패:\n{exc}")
            return
        self.status_var.set(f"CSV 저장 완료 ('{item[0]}'): {path}")

    def _default_name(self, ext):
        base = "표추출결과"
        if self.loaded_path:
            base = os.path.splitext(os.path.basename(self.loaded_path))[0] + "_표"
        return f"{base}.{ext}"

    def _to_clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)


def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    TableOCRApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
