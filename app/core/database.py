import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# .env 파일 로드
load_dotenv()

# 환경변수 읽기 (설정되어 있지 않으면 에러 발생)
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_DSN = os.getenv("DB_DSN")
WALLET_DIR = os.getenv("WALLET_DIR", "/home/ubuntu/wallet")

if not all([DB_USER, DB_PASSWORD, DB_DSN]):
    raise ValueError("DB 접속에 필요한 필수 환경변수(DB_USER, DB_PASSWORD, DB_DSN)가 설정되지 않았습니다.")

# SQLAlchemy Oracle 접속 URL
SQLALCHEMY_DATABASE_URL = f"oracle+oracledb://{DB_USER}:{DB_PASSWORD}@{DB_DSN}"

# 오라클 Wallet 연동
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={
        "config_dir": WALLET_DIR,
        "wallet_location": WALLET_DIR,
        "wallet_password": DB_PASSWORD,
    },
    pool_pre_ping=True
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()