#app/tasks/scheduler.py
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.tasks.channel_sync import sync_user_channels_task
from app.tasks.message_sync import sync_channel_messages_task
from app.tasks.daily_notice import send_daily_notice_task
from app.tasks.lifecycle import run_lifecycle_cleanup_task

logger = logging.getLogger(__name__)

# 스케줄러 공통 방어 옵션 설정
job_defaults = {
    'coalesce': True,       # 이전 작업 지연 시 누적된 작업은 1회만 병합 실행
    'max_instances': 1     # 동일 작업 concurrent 중복 실행 방지
}

scheduler = AsyncIOScheduler(
    timezone="Asia/Seoul",
    job_defaults=job_defaults
)


def start_scheduler():
    """APScheduler 작업 등록 및 시작"""
    if scheduler.running:
        logger.warning("⚠️ 스케줄러가 이미 실행 중입니다.")
        return

    now = datetime.now()

    # 1. 채널 동기화 (기본 4시간 - 앱 시작 즉시 1회 실행 후 주기적 실행)
    scheduler.add_job(
        sync_user_channels_task,
        "interval",
        hours=settings.CHANNEL_SYNC_INTERVAL_HOURS,
        id="channel_sync_job",
        next_run_time=now,  # 서버 구동 즉시 최초 1회 실행
        replace_existing=True
    )

    # 2. 메시지 수집 및 일정 C/U/D 파이프라인 (기본 1시간 - 앱 시작 즉시 1회 실행)
    scheduler.add_job(
        sync_channel_messages_task,
        "interval",
        hours=settings.MESSAGE_SYNC_INTERVAL_HOURS,
        id="message_sync_job",
        next_run_time=now,  # 서버 구동 즉시 최초 1회 실행
        replace_existing=True
    )

    # 3. 매일 아침 08:00 당일 일정 알림
    scheduler.add_job(
        send_daily_notice_task,
        "cron",
        hour=8,
        minute=0,
        id="daily_notice_job",
        replace_existing=True
    )

    # 4. 매주 일요일 새벽 03:00 라이프사이클 Cleanup
    scheduler.add_job(
        run_lifecycle_cleanup_task,
        "cron",
        day_of_week="sun",
        hour=3,
        minute=0,
        id="lifecycle_job",
        replace_existing=True
    )

    scheduler.start()
    logger.info("🚀 APScheduler 백그라운드 스케줄러가 성공적으로 시작되었습니다.")


def stop_scheduler():
    """스케줄러 안전 종료 (Graceful Shutdown)"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("🛑 APScheduler 백그라운드 스케줄러가 종료되었습니다.")