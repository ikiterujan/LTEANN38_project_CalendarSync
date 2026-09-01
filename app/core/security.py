import os
from cryptography.fernet import Fernet

# .env의 DATA_ENCRYPTION_KEY 사용 (없으면 기본 키 생성)
ENCRYPTION_KEY = os.getenv("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode())
cipher_suite = Fernet(ENCRYPTION_KEY.encode() if isinstance(ENCRYPTION_KEY, str) else ENCRYPTION_KEY)

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
        return cipher_text  # 복호화 실패 시 원본 반환 (기존 평문 호환용)