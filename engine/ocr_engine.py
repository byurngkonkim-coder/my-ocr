"""
OCR 엔진 래퍼 (PaddleOCR 기반).

- 한글/영문 등 다국어 인식 지원
- 이미지 파일과 PDF 파일 모두 처리
- PaddleOCR 3.x(predict) / 2.x(ocr) API를 모두 지원하도록 방어적으로 작성
- 한글 파일 경로 문제를 피하기 위해 이미지는 PIL로 읽어 numpy 배열로 전달
"""
from __future__ import annotations

import numpy as np
from PIL import Image

# PDF 렌더링용 (PyMuPDF). 없으면 PDF 기능만 비활성화.
try:
    import fitz  # PyMuPDF
    _HAS_FITZ = True
except Exception:  # pragma: no cover
    _HAS_FITZ = False


class OCREngine:
    """PaddleOCR을 감싼 간단한 OCR 엔진."""

    def __init__(self, lang: str = "korean"):
        self.lang = lang
        self._ocr = None  # 지연 초기화 (첫 OCR 때 모델 로드)
        self._engine_type = ""

    # ------------------------------------------------------------------ #
    # 엔진 초기화
    # ------------------------------------------------------------------ #
    def ensure_engine(self):
        """OCR 엔진을 준비한다. ONNX Runtime 기반 RapidOCR을 우선 사용하고 실패 시 PaddleOCR로 폴백한다."""
        if self._ocr is not None:
            return

        # 1. 고성능·경량 ONNX Runtime 기반 RapidOCR 우선 시도
        try:
            from rapidocr import RapidOCR
            from rapidocr.utils.typings import OCRVersion, LangRec, ModelType

            params = {}
            if self.lang and self.lang.lower() in ("korean", "kor", "ko"):
                params = {
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.lang_type": LangRec.KOREAN,
                    "Rec.model_type": ModelType.MOBILE,
                }
            self._ocr = RapidOCR(params=params if params else None)
            self._engine_type = "rapidocr"
            return
        except Exception:
            pass

        # 2. PaddleOCR 폴백
        from paddleocr import PaddleOCR
        self._engine_type = "paddleocr"
        try:
            self._ocr = PaddleOCR(
                lang=self.lang,
                use_textline_orientation=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                enable_mkldnn=False,
            )
            return
        except TypeError:
            pass

        try:
            self._ocr = PaddleOCR(lang=self.lang, use_angle_cls=True)
            return
        except TypeError:
            pass

        self._ocr = PaddleOCR(lang=self.lang)

    # ------------------------------------------------------------------ #
    # 공개 API
    # ------------------------------------------------------------------ #
    @staticmethod
    def _refine_lines(lines: list[tuple[str, float | None]]) -> list[tuple[str, float | None]]:
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
        if getattr(self, "_engine_type", "") == "rapidocr":
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

        # PaddleOCR(내부 OpenCV)은 BGR 순서를 기대하므로 채널 변환
        image_bgr = np.ascontiguousarray(image_rgb[:, :, ::-1])

        # 3.x: predict()
        if hasattr(self._ocr, "predict"):
            try:
                result = self._ocr.predict(image_bgr)
                return self._parse_v3(result)
            except Exception:
                pass  # 구버전 방식으로 폴백

        # 2.x: ocr()
        try:
            result = self._ocr.ocr(image_bgr)
        except TypeError:
            result = self._ocr.ocr(image_bgr, cls=True)
        return self._parse_v2(result)

    @staticmethod
    def _parse_v3(result):
        """PaddleOCR 3.x predict() 결과 파싱.

        결과는 dict 유사 객체의 리스트이며 'rec_texts', 'rec_scores' 키를 가진다.
        """
        lines = []
        for res in (result or []):
            texts = None
            scores = None
            try:
                texts = res["rec_texts"]
            except Exception:
                texts = getattr(res, "rec_texts", None)
            try:
                scores = res["rec_scores"]
            except Exception:
                scores = getattr(res, "rec_scores", None)

            if not texts:
                continue
            if not scores or len(scores) != len(texts):
                scores = [None] * len(texts)

            for text, score in zip(texts, scores):
                if text is not None and str(text).strip():
                    lines.append((str(text), _to_float(score)))
        return lines

    @staticmethod
    def _parse_v2(result):
        """PaddleOCR 2.x ocr() 결과 파싱.

        형태: [page, ...], page = [[box, (text, score)], ...]
        """
        lines = []
        if not result:
            return lines
        for page in result:
            if not page:
                continue
            for item in page:
                try:
                    _box, (text, score) = item
                except Exception:
                    try:
                        text, score = item[1][0], item[1][1]
                    except Exception:
                        continue
                if text is not None and str(text).strip():
                    lines.append((str(text), _to_float(score)))
        return lines


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
