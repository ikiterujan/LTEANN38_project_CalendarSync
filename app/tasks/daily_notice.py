import logging
import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.dependencies import graph_service
from app.models.domain import User
from app.models.master_calendar import MasterCalendar, UserSyncLog

logger = logging.getLogger(__name__)

async def _send_notice_to_single_user(user: User, today_schedules: list):
    """개별 유저 대상 알림 메시지 생성 및 발송"""
    schedule_items = []
    for idx, s in enumerate(today_schedules, 1):
        # KST 시간 포맷팅 (HH:MM)
        time_str = s.start_datetime.strftime("%H:%M")
        loc_str = f" ({s.location})" if s.location else ""
        schedule_items.append(f"{idx}. **{s.title}** - {time_str}{loc_str}")

    notice_message = (
        f"📅 **[오늘의 일정 알림]**\n\n"
        f"안녕하세요! 오늘 예정된 공지 일정이 총 {len(today_schedules)}건 있습니다:\n\n"
        + "\n".join(schedule_items)
    )

    try:
        await graph_service.send_teams_chat_message(user_id=user.id, message=notice_message)
    except Exception as e:
        logger.error(f"User {user.id} 알림 발송 실패: {e}")

async def send_daily_notice_task():
    """[매일 1회] Master DB 기반 당일 일정 조회 및 asyncio.gather 병렬 알림 발송"""
    db: Session = SessionLocal()
    try:
        # 1. KST 기준 오늘 00:00:00 ~ 23:59:59 범위 설정
        kst = timezone(timedelta(hours=9))
        now_kst = datetime.now(kst)
        start_of_day = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = now_kst.replace(hour=23, minute=59, second=59, microsecond=999999)

        active_users = db.query(User).filter(User.is_active == True).all()
        tasks = []

        for user in active_users:
            # 2. Master DB에서 유저별 오늘 일정 조회
            today_schedules = (
                db.query(MasterCalendar)
                .join(UserSyncLog, MasterCalendar.id == UserSyncLog.master_schedule_id)
                .filter(
                    UserSyncLog.user_id == user.id,
                    MasterCalendar.start_datetime >= start_of_day,
                    MasterCalendar.start_datetime <= end_of_day
                )
                .order_by(MasterCalendar.start_datetime.asc())
                .all()
            )

            if today_schedules:
                tasks.append(_send_notice_to_single_user(user, today_schedules))

        # 3. asyncio.gather로 병렬 발송 처리
        if tasks:
            await asyncio.gather(*tasks)
            logger.info(f"✅ 총 {len(tasks)}명 대상 당일 일정 알림 발송 완료")
        else:
            logger.info("ℹ️ 오늘 예정된 일정이 있는 유저가 없습니다.")

    except Exception as e:
        logger.error(f"❌ 당일 알림 태스크 실행 중 에러: {e}", exc_info=True)
    finally:
        db.close()