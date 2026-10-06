# -*- coding: utf-8 -*-
"""UtradeHub '사용자관리' 화면(고정 레이아웃 1788x892) 전용 추출 엔진.

MyOCR GUI의 '사용자관리(고정양식)' 모드에서 사용한다. 일반 표인식 파이프라인은
동일 양식을 매번 구조 추론하느라 느리고, ●/○ 아이콘·정렬화살표를 글자로 오독해
노이즈가 심하다. 이 엔진은 화면이 항상 같은 좌표라는 점을 이용해:

  - 텍스트 열: 일반 OCR(방향분류 OFF) 검출 결과를 고정 x/y 밴드로 셀에 매핑
  - 이용서비스 6열(●/○): OCR 대신 픽셀 채움비율로 판별 (● → Y, ○ → N)
  - 좌측 메뉴/상단 네비/페이지네이션 등은 영역 크롭+패턴 필터로 제거

TableEngine과 동일한 인터페이스(ensure_engine, recognize_image_file → [격자])를
제공하므로 GUI/일괄처리 코드에서 TableEngine 대신 그대로 쓸 수 있다.
격자의 첫 행은 헤더이며, 마지막 열 '검토필요'에 행별 품질 플래그가 담긴다.
"""
from __future__ import annotations

import re

import numpy as np
from PIL import Image

# --- 고정 좌표 (1788x892 캡처 기준, 실측값) ---
COLS_TEXT = [
    ("NO",        300,  362),
    ("사업자번호", 362,  478),
    ("업체명",     478,  846),
    ("아이디",     846,  957),
    ("가입일",    1408, 1512),
    ("회원상태",  1512, 1626),
    ("사용자구분",1626, 1726),
]
SERVICE_NAMES = ["무역업무", "e구매확인", "전자상거래", "무역원장", "AspLine", "FTA서비스"]
SERVICE_X0, SERVICE_X1 = 958.0, 1420.0
FILL_FULL, FILL_EMPTY = 0.10, 0.03          # 채움비율 ≥0.10 → ●(Y), ≤0.03 → ○(N), 사이 → 애매
TX_MIN, TX_MAX, TY_MIN, TY_MAX = 298, 1728, 198, 850
CROP_X0, CROP_Y0, CROP_X1, CROP_Y1 = 280, 190, 1740, 860
LOW_SCORE = 0.90
EXPECT_W, EXPECT_H = 1788, 892

HEADER = ["NO", "사업자번호", "업체명", "아이디"] + SERVICE_NAMES + \
         ["가입일", "회원상태", "사용자구분", "검토필요"]
BIZ_RE = re.compile(r"\d{3}-?\d{2}-?\d{4,5}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# 언어별 인식 모델 (table_engine과 동일 매핑)
LANG_REC_MODELS = {
    "korean": "korean_PP-OCRv5_mobile_rec",
    "en": "en_PP-OCRv5_mobile_rec",
    "japan": "japan_PP-OCRv5_mobile_rec",
    "ch": None,
}


class UTHFixedEngine:
    """UtradeHub 사용자관리 고정양식 추출 엔진 (TableEngine 호환)."""

    def __init__(self, lang: str = "korean"):
        self.lang = lang
        self._ocr = None

    def ensure_engine(self):
        if self._ocr is not None:
            return
        from paddleocr import PaddleOCR

        rec = LANG_REC_MODELS.get(self.lang)
        kwargs = dict(
            use_textline_orientation=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            enable_mkldnn=False,
            text_detection_model_name="PP-OCRv5_server_det",  # 정확도 우선
        )
        if rec:
            kwargs["text_recognition_model_name"] = rec
        else:
            kwargs["lang"] = self.lang
        # 미지원 파라미터가 있으면 단계적으로 제거하며 재시도
        for drop in ([],
                     ["text_detection_model_name"],
                     ["enable_mkldnn"],
                     ["text_detection_model_name", "enable_mkldnn"],
                     ["text_detection_model_name", "enable_mkldnn", "use_textline_orientation"]):
            try:
                self._ocr = PaddleOCR(**{k: v for k, v in kwargs.items() if k not in drop})
                return
            except TypeError:
                continue
        self._ocr = PaddleOCR(lang=self.lang)

    # ---------------- 공개 API (TableEngine 호환) ---------------- #
    def recognize_image_file(self, path: str) -> list[list[list[str]]]:
        """이미지 파일 → [격자] (격자 첫 행은 헤더). 데이터가 없으면 []."""
        self.ensure_engine()
        with Image.open(path) as im:
            rgb = np.array(im.convert("RGB"))
        grid = self._extract(rgb)
        return [grid] if len(grid) > 1 else []

    def recognize_pdf(self, path: str, dpi: int = 200, progress_cb=None):
        raise RuntimeError(
            "고정양식 모드는 캡처 이미지 전용입니다. PDF는 '표 추출' 모드를 사용하세요.")

    def is_expected_size(self, path: str) -> bool:
        try:
            with Image.open(path) as im:
                return im.size == (EXPECT_W, EXPECT_H)
        except Exception:
            return False

    # ---------------- 내부 ---------------- #
    def _boxes(self, bgr, ox, oy):
        out = []
        for r in self._ocr.predict(bgr):
            texts, boxes, scores = r["rec_texts"], r["rec_boxes"], r["rec_scores"]
            for i, t in enumerate(texts):
                b = np.array(boxes[i])
                if b.ndim == 1:
                    x1, y1, x2, y2 = map(float, b[:4])
                else:
                    x1, y1 = float(b[:, 0].min()), float(b[:, 1].min())
                    x2, y2 = float(b[:, 0].max()), float(b[:, 1].max())
                out.append((str(t).strip(), (x1 + x2) / 2 + ox, (y1 + y2) / 2 + oy, float(scores[i])))
        return out

    @staticmethod
    def _col_of(xc):
        for name, a, b in COLS_TEXT:
            if a <= xc < b:
                return name
        return None

    def _services(self, gray, yc):
        bw = (SERVICE_X1 - SERVICE_X0) / 6.0
        flags, amb = [], []
        for i in range(6):
            cx = SERVICE_X0 + (i + 0.5) * bw
            cell = gray[max(0, int(yc - 11)):int(yc + 11), max(0, int(cx - 15)):int(cx + 15)]
            frac = float(np.mean(cell < 110)) if cell.size else 0.0
            flags.append("Y" if frac >= FILL_EMPTY + (FILL_FULL - FILL_EMPTY) / 2 else "N")
            if FILL_EMPTY < frac < FILL_FULL:
                amb.append(SERVICE_NAMES[i])
        return flags, amb

    def _extract(self, rgb):
        gray = np.array(Image.fromarray(rgb).convert("L"))
        h, w = rgb.shape[:2]
        cx0, cy0 = max(0, CROP_X0), max(0, CROP_Y0)
        cx1, cy1 = min(w, CROP_X1), min(h, CROP_Y1)
        bgr = np.ascontiguousarray(rgb[cy0:cy1, cx0:cx1][:, :, ::-1])
        boxes = [b for b in self._boxes(bgr, cx0, cy0)
                 if TX_MIN <= b[1] <= TX_MAX and TY_MIN <= b[2] <= TY_MAX and b[0]]
        boxes.sort(key=lambda z: z[2])
        clusters = []
        for b in boxes:
            if clusters and abs(b[2] - clusters[-1][0]) <= 16:
                clusters[-1][1].append(b)
            else:
                clusters.append([b[2], [b]])

        grid = [list(HEADER)]
        for _y, items in clusters:
            cells = {name: "" for name, _, _ in COLS_TEXT}
            cscore = {}
            yc = float(np.median([it[2] for it in items]))
            for t, xc, _yy, sc in sorted(items, key=lambda z: z[1]):
                c = self._col_of(xc)
                if c:
                    cells[c] = (cells[c] + " " + t).strip() if cells[c] else t
                    cscore[c] = min(cscore.get(c, 1.0), sc)
            if not (BIZ_RE.search(cells["사업자번호"]) or DATE_RE.search(cells["가입일"])):
                continue  # 페이지네이션/잡음 행 제거
            flags, amb = self._services(gray, yc)
            q = []
            if not re.search(r"\d", cells["NO"]):
                q.append("NO확인")
            if not BIZ_RE.search(cells["사업자번호"]):
                q.append("사업자번호확인")
            if not cells["업체명"]:
                q.append("업체명없음")
            if not cells["아이디"]:
                q.append("아이디없음")
            if not DATE_RE.search(cells["가입일"]):
                q.append("가입일확인")
            idsc = min(cscore.get("사업자번호", 1.0), cscore.get("아이디", 1.0))
            if idsc < LOW_SCORE:
                q.append(f"저신뢰{idsc:.2f}")
            if amb:
                q.append("서비스애매(" + ",".join(amb) + ")")
            grid.append([cells["NO"], cells["사업자번호"], cells["업체명"], cells["아이디"],
                         *flags, cells["가입일"], cells["회원상태"], cells["사용자구분"], ";".join(q)])
        return grid
