# app/services/ocr_service.py
import io
import logging
from PIL import Image
import numpy as np

logger = logging.getLogger(__name__)

class EasyOCRService:
    def __init__(self):
        self._reader = None

    @property
    def reader(self):
        # 처음 OCR 요청이 들어올 때만 모델을 메모리에 로드 (Lazy Initialization)
        if self._reader is None:
            logger.info("EasyOCR Reader 모델을 초기화합니다...")
            import easyocr
            self._reader = easyocr.Reader(['ko', 'en'], gpu=False)
        return self._reader

    def extract_text(self, image_bytes: bytes) -> str:
        """이미지 바이트 데이터를 받아 OCR 텍스트를 추출합니다."""
        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            image_np = np.array(image)
            
            results = self.reader.readtext(image_np, detail=0)
            
            extracted_text = "\n".join(results).strip()
            return extracted_text
            
        except Exception as e:
            logger.error(f"EasyOCR 텍스트 추출 중 에러 발생: {e}", exc_info=True)
            return ""