"""
OCR 엔진 래퍼 — RapidOCR(ONNX Runtime, 한국어는 PP-OCRv5 한글 모델) 하나만 쓴다.

- 한글/영문 등 다국어 인식 지원
- 이미지 파일과 PDF 파일 모두 처리
- 한글 파일 경로 문제를 피하기 위해 이미지는 PIL로 읽어 numpy 배열로 전달
- 예전 Paddle 계열 예비 엔진은 2026-10-13 제거 (본문·표·고정양식 모두 RapidOCR 로 일원화)
"""
from __future__ import annotations

import numpy as np
from PIL import Image

# PDF 렌더링용 (PyMuPDF). 없으면 PDF 기능만 비활성화.
# 'import fitz' 는 매 실행 deprecated 경고를 내므로 새 이름으로 불러온다
try:
    import pymupdf as fitz
    _HAS_FITZ = True
except Exception:  # pragma: no cover
    _HAS_FITZ = False


def rapidocr_params(lang: str):
    """언어별 RapidOCR 설정. 인식 모델을 정하지 않은 언어('ch')는 기본 모델(중국어+영어).

    OCREngine·TableEngine·UTHFixedEngine 이 함께 쓴다. 일본어는 PP-OCRv5 모델이 없어 v4.
    글자 방향 판별(cls)은 끈다 — 짧은 단어를 뒤집힌 글자로 보고 180° 돌려 읽었다
    (표 샘플 '직급'→'큰'·'연봉'→'움', 12칸 중 6칸만 맞음 → 끄면 12칸 모두. 2026-10-13).
    예전 Paddle 계열 경로는 방향 판별을 끄고(use_textline_orientation=False) 있었다.
    """
    from rapidocr.utils.typings import OCRVersion, LangRec, ModelType
    params = {"Global.use_cls": False}
    rec = {"korean": (OCRVersion.PPOCRV5, LangRec.KOREAN),
           "en": (OCRVersion.PPOCRV5, LangRec.EN),
           "japan": (OCRVersion.PPOCRV4, LangRec.JAPAN)}.get((lang or "").lower())
    if rec is not None:
        params.update({"Rec.ocr_version": rec[0], "Rec.lang_type": rec[1], "Rec.model_type": ModelType.MOBILE})
    return params


def engine_error(exc: Exception) -> RuntimeError:
    """RapidOCR 를 준비하지 못했을 때 — 진짜 원인과 복구 방법을 알린다."""
    return RuntimeError(
        f"OCR 엔진(RapidOCR)을 준비하지 못했습니다: {type(exc).__name__}: {exc}\n"
        "(engine\\install.bat 을 다시 실행해 보세요.)")


def new_rapidocr(lang: str):
    """언어별 모델로 RapidOCR 을 만든다. 실패하면 원인을 담은 RuntimeError."""
    try:
        from rapidocr import RapidOCR
        return RapidOCR(params=rapidocr_params(lang))
    except Exception as exc:
        raise engine_error(exc) from exc


class OCREngine:
    """RapidOCR 을 감싼 간단한 OCR 엔진."""

    def __init__(self, lang: str = "korean"):
        self.lang = lang
        self._ocr = None  # 지연 초기화 (첫 OCR 때 모델 로드)

    # ------------------------------------------------------------------ #
    # 엔진 초기화
    # ------------------------------------------------------------------ #
    def ensure_engine(self):
        """OCR 엔진(RapidOCR)을 준비한다."""
        if self._ocr is None:
            self._ocr = new_rapidocr(self.lang)

    # ------------------------------------------------------------------ #
    # 공개 API
    # ------------------------------------------------------------------ #
    def _refine_lines(self, lines: list[tuple[str, float | None]]) -> list[tuple[str, float | None]]:
        if self.lang != "korean":   # 한국어 교정 규칙을 영어·중국어·일본어 결과에 적용하지 않는다
            return lines
        try:
            from korean_corrector import refine_korean_text
            return [(refine_korean_text(text), score) for text, score in lines]
        except Exception:
            return lines

    def ocr_image_file(self, path: str, refine: bool = True):
        """이미지 파일 경로 -> [(text, score), ...]"""
        with Image.open(path) as im:
            rgb = np.array(im.convert("RGB"))
        lines = self._run(rgb)
        return self._refine_lines(lines) if refine else lines

    def ocr_pil(self, pil_image: Image.Image, refine: bool = True):
        """PIL 이미지 -> [(text, score), ...]"""
        rgb = np.array(pil_image.convert("RGB"))
        lines = self._run(rgb)
        return self._refine_lines(lines) if refine else lines

    def ocr_pdf(self, path: str, dpi: int = 200, progress_cb=None, force_ocr: bool = False, refine: bool = True):
        """PDF 파일 -> 페이지별 [[(text, score), ...], ...]

        progress_cb(current_page, total_pages) 콜백으로 진행 상황을 알린다.
        force_ocr=False이면 텍스트 레이어가 있는 디지털 PDF 페이지는 OCR 없이
        PyMuPDF 내장 텍스트를 즉시 반환하여 불필요한 연산과 지연을 방지한다.
        refine=True이면 한국어 띄어쓰기, 맞춤법 및 OCR 노이즈 정제 규칙을 자동 적용한다.
        """
        if not _HAS_FITZ:
            raise RuntimeError(
                "PDF 처리를 위해 PyMuPDF가 필요합니다. (pip install pymupdf)"
            )
        doc = fitz.open(path)
        try:
            total = doc.page_count
            zoom = dpi / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pages = []
            for i in range(total):
                page = doc.load_page(i)

                # 디지털 텍스트 레이어 존재 여부 확인 (Bypass 필터)
                if not force_ocr:
                    raw_text = page.get_text("text").strip()
                    if len("".join(raw_text.split())) >= 20:
                        lines = []
                        for line in raw_text.splitlines():
                            l_str = line.strip()
                            if l_str:
                                lines.append((l_str, 1.0))
                        if refine:
                            lines = self._refine_lines(lines)
                        pages.append(lines)
                        if progress_cb:
                            progress_cb(i + 1, total)
                        continue

                # 텍스트 레이어가 없는 스캔 페이지는 이미지 렌더링 후 OCR 실행
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
                rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, 3
                )
                page_lines = self._run(np.ascontiguousarray(rgb))
                if refine:
                    page_lines = self._refine_lines(page_lines)
                pages.append(page_lines)
                if progress_cb:
                    progress_cb(i + 1, total)
            return pages
        finally:
            doc.close()

    # ------------------------------------------------------------------ #
    # 내부 실행/파싱
    # ------------------------------------------------------------------ #
    def _run(self, image_rgb: np.ndarray):
        """RGB numpy 이미지를 받아 [(text, score), ...] 반환."""
        self.ensure_engine()
        out = self._ocr(image_rgb)
        lines = []
        if out is not None and getattr(out, "txts", None):
            txts = out.txts or ()
            scores = out.scores or ()
            for idx, text in enumerate(txts):
                if text and str(text).strip():
                    s = _to_float(scores[idx]) if idx < len(scores) else 1.0
                    lines.append((str(text).strip(), s))
        return lines


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
