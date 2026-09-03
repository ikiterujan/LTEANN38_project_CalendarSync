#reset.py
from app.core.database import Base, engine

def resetdb():
    # 모든 테이블 삭제 후 다시 생성
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)