#app/core/security.py
import logging
from cryptography.fernet import Fernet
from app.core.config import settings

logger = logging.getLogger(__name__)

# DATA_ENCRYPTION_KEY는 config.py에서 필수(Required) 필드로 강제되므로
# 값이 없으면 앱 기동 시점(Settings 로드 단계)에서 즉시 실패한다.
# 여기서 임의 키를 생성해 fallback하면 재시작마다 키가 바뀌어
# 기존 암호화 데이터를 복호화할 수 없게 되는 데이터 유실 위험이 있으므로 금지한다.
cipher_suite = Fernet(settings.DATA_ENCRYPTION_KEY.encode())


def encrypt_text(plain_text: str) -> str:
    if not plain_text:
        return plain_text
    return cipher_suite.encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_text(cipher_text: str) -> str:
    if not cipher_text:
        return cipher_text
    try:
        return cipher_suite.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
    except Exception:
        logger.warning("복호화 실패: 키 불일치 또는 손상된 데이터일 수 있음 (원본 값을 그대로 반환)")
        return cipher_text  # 복호화 실패 시 원본 반환 (기존 평문 호환용)