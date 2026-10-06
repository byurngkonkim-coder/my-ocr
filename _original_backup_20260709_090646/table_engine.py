"""
표(테이블) 인식 엔진 - PaddleOCR TableRecognitionPipelineV2 기반.

캡처된 표 이미지/PDF에서 표 구조(행·열·셀)를 인식하고, 각 셀의 텍스트를
정확히 추출하여 2차원 격자(grid)로 복원한다. 결과는 GUI 격자 표시 및
엑셀/CSV/TSV 내보내기에 사용된다.

- 한글 셀 텍스트 정확도를 위해 언어별 인식 모델을 지정한다.
- 표 구조는 pred_html(HTML <table>)로 나오며, 이를 직접 파싱해 격자로 만든다.
"""
from __future__ import annotations

from html.parser import HTMLParser

import numpy as np
from PIL import Image

try:
    import fitz  # PyMuPDF (PDF 렌더링)
    _HAS_FITZ = True
except Exception:  # pragma: no cover
    _HAS_FITZ = False


# 화면 표시 언어 -> PaddleOCR 인식 모델명 (None이면 기본 모델 사용)
LANG_REC_MODELS = {
    "korean": "korean_PP-OCRv5_mobile_rec",
    "en": "en_PP-OCRv5_mobile_rec",
    "japan": "japan_PP-OCRv5_mobile_rec",
    "ch": None,  # 기본 PP-OCRv5 모델(중국어+영어)
}


# --------------------------------------------------------------------------- #
# HTML <table> -> 2차원 격자 파서 (colspan/rowspan 처리)
# --------------------------------------------------------------------------- #
class _TableHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, int, int]]] = []
        self._row: list[tuple[str, int, int]] | None = None
        self._in_cell = False
        self._buf: list[str] = []
        self._span = (1, 1)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            a = dict(attrs)
            self._in_cell = True
            self._buf = []
            self._span = (_int(a.get("colspan"), 1), _int(a.get("rowspan"), 1))
        elif tag == "br" and self._in_cell:
            self._buf.append(" ")

    def handle_data(self, data):
        if self._in_cell:
            self._buf.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self._in_cell:
            text = " ".join("".join(self._buf).split())
            self._row.append((text, self._span[0], self._span[1]))
            self._in_cell = False
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _int(value, default):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def html_table_to_grid(html: str) -> list[list[str]]:
    """HTML <table> 문자열을 2차원 문자열 격자로 변환한다.

    colspan/rowspan은 좌상단 셀에 텍스트를 넣고 나머지 확장 칸은 빈 문자열로 채운다.
    """
    parser = _TableHTMLParser()
    parser.feed(html or "")

    grid: list[list[str]] = []
    occupied: set[tuple[int, int]] = set()

    for r, row in enumerate(parser.rows):
        c = 0
        for text, colspan, rowspan in row:
            while (r, c) in occupied:
                c += 1
            for dr in range(rowspan):
                for dc in range(colspan):
                    rr, cc = r + dr, c + dc
                    while len(grid) <= rr:
                        grid.append([])
                    while len(grid[rr]) <= cc:
                        grid[rr].append("")
                    if dr == 0 and dc == 0:
                        grid[rr][cc] = text
                    else:
                        occupied.add((rr, cc))
            c += colspan

    # 모든 행의 열 개수를 최대값으로 맞춤
    max_cols = max((len(row) for row in grid), default=0)
    for row in grid:
        row.extend([""] * (max_cols - len(row)))
    return grid


def _upscale_if_small(rgb: np.ndarray, min_side: int = 1000,
                      max_scale: float = 4.0, max_side: int = 4000) -> np.ndarray:
    """표 셀 검출 정확도를 위해 작은 이미지를 확대한다.

    짧은 변이 min_side보다 작으면 최대 max_scale배까지 키운다(긴 변은 max_side로 제한).
    이미 충분히 크면 원본 그대로 반환한다.
    """
    h, w = rgb.shape[:2]
    short = min(h, w)
    if short >= min_side:
        return rgb
    scale = min(max_scale, min_side / short)
    if max(h, w) * scale > max_side:
        scale = max_side / max(h, w)
    if scale <= 1.01:
        return rgb
    new_size = (int(round(w * scale)), int(round(h * scale)))
    return np.array(Image.fromarray(rgb).resize(new_size, Image.LANCZOS))


# --------------------------------------------------------------------------- #
# 표 인식 엔진
# --------------------------------------------------------------------------- #
class TableEngine:
    def __init__(self, lang: str = "korean"):
        self.lang = lang
        self._pipe = None  # 지연 초기화

    def ensure_engine(self):
        """파이프라인 준비 (최초 호출 시 모델 로드/다운로드)."""
        if self._pipe is not None:
            return
        from paddleocr import TableRecognitionPipelineV2

        kwargs = dict(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            device="cpu",
            enable_mkldnn=False,  # paddlepaddle 3.x oneDNN(PIR) 버그 회피 (필수)
        )
        rec_model = LANG_REC_MODELS.get(self.lang)
        if rec_model:
            kwargs["text_recognition_model_name"] = rec_model
        try:
            self._pipe = TableRecognitionPipelineV2(**kwargs)
        except TypeError:
            # enable_mkldnn 미지원 구버전 대비
            kwargs.pop("enable_mkldnn", None)
            self._pipe = TableRecognitionPipelineV2(**kwargs)

    # ---------------------------- 공개 API ---------------------------- #
    def recognize_image_file(self, path: str) -> list[list[list[str]]]:
        """이미지 파일 -> 표 목록(각 표는 2차원 격자)."""
        with Image.open(path) as im:
            rgb = np.array(im.convert("RGB"))
        return self._recognize(rgb)

    def recognize_pdf(self, path: str, dpi: int = 200, progress_cb=None):
        """PDF -> [(페이지번호, [표격자, ...]), ...]"""
        if not _HAS_FITZ:
            raise RuntimeError("PDF 처리를 위해 PyMuPDF가 필요합니다. (pip install pymupdf)")
        doc = fitz.open(path)
        try:
            total = doc.page_count
            zoom = dpi / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pages = []
            for i in range(total):
                page = doc.load_page(i)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
                rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, 3
                )
                tables = self._recognize(np.ascontiguousarray(rgb))
                pages.append((i + 1, tables))
                if progress_cb:
                    progress_cb(i + 1, total)
            return pages
        finally:
            doc.close()

    # ---------------------------- 내부 ---------------------------- #
    def _recognize(self, image_rgb: np.ndarray) -> list[list[list[str]]]:
        self.ensure_engine()
        image_rgb = _upscale_if_small(image_rgb)   # 저해상도 표는 셀 검출이 어긋나므로 확대
        image_bgr = np.ascontiguousarray(image_rgb[:, :, ::-1])  # RGB -> BGR
        output = self._pipe.predict(image_bgr)

        tables: list[list[list[str]]] = []
        for res in output:
            table_list = _get(res, "table_res_list") or []
            for table in table_list:
                html = _get(table, "pred_html")
                if not html:
                    continue
                grid = html_table_to_grid(html)
                if grid:
                    tables.append(grid)
        return tables


def _get(obj, key):
    """dict 유사 객체/일반 객체 모두에서 안전하게 값 추출."""
    try:
        return obj[key]
    except Exception:
        return getattr(obj, key, None)
