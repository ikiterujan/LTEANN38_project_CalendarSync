# app/models/types.py
from sqlalchemy.types import TypeDecorator, String
from app.core.security import encrypt_text, decrypt_text


class EncryptedString(TypeDecorator):
    """
    SQLAlchemy DB 입출력 시 자동으로 AES-256(Fernet) 암/복호화를 수행하는 커스텀 타입.
    
    - DB 저장 시 (process_bind_param): 평문 -> 암호문
    - DB 조회 시 (process_result_value): 암호문 -> 평문 (ORM 조회, select() 컬럼 projection 조회 모두 자동 적용)
    """
    impl = String
    cache_ok = True

    def __init__(self, length=None, **kwargs):
        super().__init__(length=length, **kwargs)

    def process_bind_param(self, value, dialect):
        if value is not None:
            return encrypt_text(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return decrypt_text(value)
        return value