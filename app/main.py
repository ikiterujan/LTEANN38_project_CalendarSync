from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.tasks.scheduler import scheduler, start_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 앱 시작 시 스케줄러 실행
    start_scheduler()
    yield
    # 앱 종료 시 스케줄러 안전 종료
    scheduler.shutdown()

app = FastAPI(title="Teams-Outlook Sync Engine", lifespan=lifespan)

@app.get("/health")
def health_check():
    return {"status": "ok"}