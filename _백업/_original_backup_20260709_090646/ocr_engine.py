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

    # ------------------------------------------------------------------ #
    # 엔진 초기화
    # ------------------------------------------------------------------ #
    def ensure_engine(self):
        """PaddleOCR 객체를 준비한다. 최초 호출 시 모델을 로드/다운로드한다."""
        if self._ocr is not None:
            return
        from paddleocr import PaddleOCR

        # PaddleOCR 3.x 파라미터로 먼저 시도, 안 되면 구버전 파라미터로 폴백
        try:
            self._ocr = PaddleOCR(
                lang=self.lang,
                use_textline_orientation=True,     # 기울어진 텍스트 줄 보정
                use_doc_orientation_classify=False,  # 무거운 전처리 모델은 끔
                use_doc_unwarping=False,
                enable_mkldnn=False,  # paddlepaddle 3.x oneDNN(PIR) 버그 회피 (필수)
            )
            return
        except TypeError:
            pass

        try:  # 2.x 계열
            self._ocr = PaddleOCR(lang=self.lang, use_angle_cls=True)
            return
        except TypeError:
            pass

        # 최소 파라미터
        self._ocr = PaddleOCR(lang=self.lang)

    # ------------------------------------------------------------------ #
    # 공개 API
    # ------------------------------------------------------------------ #
    def ocr_image_file(self, path: str):
        """이미지 파일 경로 -> [(text, score), ...]"""
        with Image.open(path) as im:
            rgb = np.array(im.convert("RGB"))
        return self._run(rgb)

    def ocr_pil(self, pil_image: Image.Image):
        """PIL 이미지 -> [(text, score), ...]"""
        rgb = np.array(pil_image.convert("RGB"))
        return self._run(rgb)

    def ocr_pdf(self, path: str, dpi: int = 200, progress_cb=None):
        """PDF 파일 -> 페이지별 [[(text, score), ...], ...]

        progress_cb(current_page, total_pages) 콜백으로 진행 상황을 알린다.
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
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
                rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                    pix.height, pix.width, 3
                )
                pages.append(self._run(np.ascontiguousarray(rgb)))
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
