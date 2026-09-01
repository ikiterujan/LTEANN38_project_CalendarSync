# main.py
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.tasks.scheduler import scheduler, start_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Gunicorn/Uvicorn Multi-Worker 환경 중복 실행 방지 (기본 메인 워커에서만 구동)
    # 별도 스케줄러 컨테이너/프로세스로 띄울 경우 인프라 레벨 분리 가능
    if os.environ.get("RUN_SCHEDULER", "true").lower() == "true":
        start_scheduler()
    
    yield
    
    if scheduler.running:
        scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/health")
def health_check():
    return {"status": "ok"}