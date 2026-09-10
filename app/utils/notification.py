# app/services/user_notice_service.py
import logging
from typing import List, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.dependencies import bot_service
from app.core.timezone import now_kst
from app.models.domain import User
from app.models.master_calendar import MasterCalendar, UserSyncLog

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def get_single_user_today_schedules(db: Session, user_id: str) -> List[Dict[str, Any]]:
    """단일 유저의 오늘(KST) 일정을 쿼리하여 Primitive Dict 리스트로 반환"""
    today_kst = now_kst()
    start_of_day = today_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = today_kst.replace(hour=23, minute=59, second=59, microsecond=999999)

    stmt = (
        select(
            MasterCalendar.title,
            MasterCalendar.start_datetime,
            MasterCalendar.location,
            MasterCalendar.description
        )
        .select_from(UserSyncLog)
        .join(MasterCalendar, UserSyncLog.master_schedule_id == MasterCalendar.id)
        .join(User, UserSyncLog.user_id == User.id)
        .where(
            UserSyncLog.user_id == user_id,
            User.is_active == True,
            MasterCalendar.start_datetime >= start_of_day,
            MasterCalendar.start_datetime <= end_of_day
        )
        .order_by(MasterCalendar.start_datetime.asc())
    )

    results = db.execute(stmt).all()

    return [
        {
            "title": title,
            "start_dt": start_dt,
            "location": location,
            "description": description
        }
        for title, start_dt, location, description in results
    ]


def format_schedule_message(schedules: List[Dict[str, Any]]) -> str:
    """조회된 일정 데이터 리스트를 Teams 전송용 Markdown 텍스트로 포맷팅"""
    if not schedules:
        return "📅 **[오늘의 일정]**\n\n오늘 예정된 공지 일정이 없습니다."

    schedule_text_list = []
    for idx, item in enumerate(schedules, 1):
        start_dt: datetime = item["start_dt"]
        time_str = start_dt.strftime("%H:%M") if isinstance(start_dt, datetime) else "시간 미정"
        loc_str = f" ({item['location']})" if item.get("location") else ""
        schedule_text_list.append(f"{idx}. **{item['title']}** - `{time_str}`{loc_str}")

    return (
        f"📅 **[오늘의 일정 알림]**\n\n"
        f"오늘 예정된 공지 일정이 총 {len(schedules)}건 있습니다:\n\n"
        + "\n".join(schedule_text_list)
    )


async def send_today_notice_to_user(db: Session, user_id: str) -> Dict[str, Any]:
    """[즉시 발송용] 단일 유저의 오늘 일정을 조회하여 Teams 챗 메시지로 전송"""
    try:
        user_stmt = select(User).where(User.id == user_id, User.is_active == True)
        user = db.execute(user_stmt).scalar_one_or_none()
    
        if not user or not user.conversation_id or not user.service_url:
            logger.warning(f"User {user_id}의 Bot 대화 정보(conversation_id / service_url)가 없습니다.")
            return {"success": False, "reason": "Bot conversation info missing", "count": 0}
        schedules = get_single_user_today_schedules(db, user_id)
        notice_message = format_schedule_message(schedules)

        await bot_service.send_teams_reply(
            service_url=user.service_url,
            conversation_id=user.conversation_id,
            message=notice_message,
        )

        return {"success": True, "count": len(schedules), "message": notice_message}
    except Exception as e:
        logger.error(f"User 단일 알림 발송 실패: {e}", exc_info=True)
        return {"success": False, "reason": str(e), "count": 0}