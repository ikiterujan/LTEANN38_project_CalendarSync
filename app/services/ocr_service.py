#app/services/ocr_service.py
import io
import logging
from PIL import Image
import numpy as np
import easyocr

logger = logging.getLogger(__name__)

class EasyOCRService:
    def __init__(self):
        # reader 모델 초기화 (한국어 'ko', 영어 'en' 지정)
        # 앱 시작 시 1회만 로드하여 메모리 재사용
        logger.info("EasyOCR Reader 모델을 로딩합니다...")
        self.reader = easyocr.Reader(['ko', 'en'], gpu=False)  # GPU 서버 환경이라면 gpu=True

    def extract_text(self, image_bytes: bytes) -> str:
        """이미지 바이트 데이터를 받아 OCR 텍스트를 추출합니다."""
        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            image_np = np.array(image)
            
            # detail=0 : 위치 좌표 없이 텍스트 문자열만 리스트로 반환
            results = self.reader.readtext(image_np, detail=0)
            
            extracted_text = "\n".join(results).strip()
            return extracted_text
            
        except Exception as e:
            logger.error(f"EasyOCR 텍스트 추출 중 에러 발생: {e}", exc_info=True)
            return ""