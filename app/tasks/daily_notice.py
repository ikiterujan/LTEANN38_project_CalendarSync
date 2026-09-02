import logging
import asyncio
from typing import List, Dict, Any, Tuple
from collections import defaultdict
from datetime import datetime
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.dependencies import graph_service
from app.core.timezone import now_kst
from app.models.domain import User
from app.models.master_calendar import MasterCalendar, UserSyncLog

logger = logging.getLogger(__name__)


async def _send_notice_to_single_user(user_id: str, schedule_items_info: List[Dict[str, Any]]):
    """
    개별 유저 대상 알림 메시지 포맷팅 및 발송 (Primitive Data만 전달받아 실행)
    """
    schedule_text_list = []
    for idx, item in enumerate(schedule_items_info, 1):
        # KST 시간 포맷팅 (HH:MM)
        time_str = item["start_dt"].strftime("%H:%M")
        loc_str = f" ({item['location']})" if item.get("location") else ""
        schedule_text_list.append(f"{idx}. **{item['title']}** - {time_str}{loc_str}")

    notice_message = (
        f"📅 **[오늘의 일정 알림]**\n\n"
        f"안녕하세요! 오늘 예정된 공지 일정이 총 {len(schedule_items_info)}건 있습니다:\n\n"
        + "\n".join(schedule_text_list)
    )

    try:
        await graph_service.send_teams_chat_message(user_id=user_id, message=notice_message)
    except Exception as e:
        logger.error(f"User {user_id} 알림 발송 실패: {e}")


async def send_daily_notice_task():
    """[매일 1회] 단일 쿼리로 당일 일정 일괄 조회 후 asyncio.gather 병렬 알림 발송"""
    db: Session = SessionLocal()
    try:
        # 1. KST 기준 오늘 00:00:00 ~ 23:59:59 범위 설정
        today_kst = now_kst()
        start_of_day = today_kst.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = today_kst.replace(hour=23, minute=59, second=59, microsecond=999999)

        # 2. N+1 쿼리 방지: 단 1회의 JOIN 쿼리로 오늘 일정이 있는 유저들의 스칼라 데이터만 일괄 조회
        results: List[Tuple[str, str, datetime, str]] = (
            db.query(
                UserSyncLog.user_id,
                MasterCalendar.title,
                MasterCalendar.start_datetime,
                MasterCalendar.location
            )
            .join(MasterCalendar, UserSyncLog.master_schedule_id == MasterCalendar.id)
            .join(User, UserSyncLog.user_id == User.id)
            .filter(
                User.is_active == True,
                MasterCalendar.start_datetime >= start_of_day,
                MasterCalendar.start_datetime <= end_of_day
            )
            .order_by(MasterCalendar.start_datetime.asc())
            .all()
        )

        if not results:
            logger.info("ℹ️ 오늘 예정된 일정이 있는 유저가 없습니다.")
            return

        # 3. 조회 결과를 유저 ID별로 Grouping (메모리 내 딕셔너리 정렬)
        user_schedules_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for user_id, title, start_dt, location in results:
            user_schedules_map[user_id].append({
                "title": title,
                "start_dt": start_dt,
                "location": location
            })

        # 4. ORM 객체 생략 및 Primitive 데이터만 태스크로 전달
        tasks = [
            _send_notice_to_single_user(user_id, schedules)
            for user_id, schedules in user_schedules_map.items()
        ]

        # 5. asyncio.gather로 안전하게 병렬 발송
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info(f"✅ 총 {len(tasks)}명 대상 당일 일정 알림 발송 작업 완료")

    except Exception as e:
        logger.error(f"❌ 당일 알림 태스크 실행 중 에러: {e}", exc_info=True)
    finally:
        db.expunge_all()
        db.close()