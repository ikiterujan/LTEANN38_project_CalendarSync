import oracledb
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

# 1. oracledb 라이브러리 활용: Wallet 기본 설정 적용 (oracledb 변수 직접 사용)
oracledb.defaults.config_dir = settings.WALLET_DIR

# 2. (선택사항) Thick 모드가 필요한 경우 oracledb 사용
# oracledb.init_oracle_client()

# SQLAlchemy Oracle 접속 URL
SQLALCHEMY_DATABASE_URL = (
    f"oracle+oracledb://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_DSN}"
)

# 오라클 Wallet 및 Connection Pool 설정
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={
        "config_dir": settings.WALLET_DIR,
        "wallet_location": settings.WALLET_DIR,
        "wallet_password": settings.DB_PASSWORD,
    },
    pool_size=10,             # 커넥션 풀 기본 크기
    max_overflow=20,          # 순간 부하 시 추가 허용 커넥션
    pool_recycle=3600,        # 1시간마다 커넥션 재생성 (Oracle Timeout 방지)
    pool_pre_ping=True        # 끊어진 커넥션 감지 후 자동 재연결
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()