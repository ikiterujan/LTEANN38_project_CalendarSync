#main.py
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.tasks.scheduler import start_scheduler, stop_scheduler
from app.reset import resetdb
from app.core.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.RESET_DB:
        logger.warning("Resetting DB...")
        resetdb()
        
    # Gunicorn/Uvicorn Multi-Worker 환경 중복 실행 방지
    # (단일 컨테이너 내 워커 분리 또는 독립 스케줄러 프로세스 제어용)
    should_run_scheduler = os.environ.get("RUN_SCHEDULER", "true").lower() == "true"
    
    if should_run_scheduler:
        logger.info("Initializing APScheduler...")
        start_scheduler()
    
    yield
    
    if should_run_scheduler:
        logger.info("Shutting down APScheduler...")
        stop_scheduler()

app = FastAPI(
    title="Teams Sync Service",
    lifespan=lifespan
)

@app.get("/health")
def health_check():
    return {"status": "ok"}