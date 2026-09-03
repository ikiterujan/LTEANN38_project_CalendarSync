#main.py
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.tasks.scheduler import start_scheduler, stop_scheduler
from app.reset import resetdb
from app.core.config import settings
from app.endpoints.webhook import router as webhook_router
import sys

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.RESET_DB:
        logger.warning("Resetting DB...")
        resetdb()
        
    logger.info("Initializing APScheduler...")
    start_scheduler()
    
    yield
    
    logger.info("Shutting down APScheduler...")
    stop_scheduler()

app = FastAPI(
    title="Teams Sync Service",
    lifespan=lifespan
)

app.include_router(webhook_router)

@app.get("/health")
def health_check():
    return {"status": "ok"}