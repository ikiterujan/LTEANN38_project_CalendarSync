# app/endpoints/webhook.py
import re
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Request, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.core.database import get_db
from app.models.domain import User
from app.core.config import settings
from app.core.dependencies import bot_service, graph_service
from cachetools import TTLCache

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Webhook"])

RECENT_SYNC_REQUESTS: TTLCache[str, float] = TTLCache(
    maxsize=100, 
    ttl=settings.DUPLICATE_WEBHOOK_DEBOUNCE_SECONDS
)

def calculate_grade_from_email(email: Optional[str], current_year: int) -> Optional[int]:
    """이메일 아이디의 앞 2자리 숫자를 입학 연도로 판단하여 학년을 계산합니다."""
    if not email:
        return None
    
    username = email.split('@')[0].strip()
    if username in settings.GRADE_OVERRIDES:
        var = settings.GRADE_OVERRIDES[username]
        return int(var) if var is not None else None
    
    match = re.match(r'^(\d{2})', username)
    if match:
        entry_year_suffix = int(match.group(1))
        entry_year = 2000 + entry_year_suffix
        grade = current_year - entry_year + 1
        
        if 1 <= grade <= 3:
            return grade
    return None


@router.post("/api/messages")
async def teams_event_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Teams Bot Framework 이벤트 수신 및 lightweight select 기반 유저 처리"""
    data = await request.json()
    activity_type = data.get("type")
    now_utc = datetime.now(timezone.utc)
    now_ts = now_utc.timestamp()

    from_user = data.get("from", {})
    user_id = from_user.get("aadObjectId") or from_user.get("id")
    user_conversation = data.get("conversation") or {}
    user_conversation_id = user_conversation.get("id")
    service_url = data.get("serviceUrl")

    # 이메일 및 학년 계산
    try:
        # graph_service의 유저 정보 조회 메서드 호출 (메서드명은 프로젝트 환경에 맞춰 확인 필요)
        user_info = await graph_service.get_user(user_id)
        if user_info:
            user_email = user_info.get("mail") or user_info.get("userPrincipalName")
            logger.info(f"🔍 [Graph API] Email 조회 성공: User({user_id}) -> {user_email}")
    except Exception as ge:
        logger.warning(f"⚠️ [Graph API] User({user_id}) 이메일 조회 실패: {ge}")
        
    user_grade = calculate_grade_from_email(user_email, now_utc.year)

    if not user_id:
        return {"status": "ok", "message": "no_user_id_in_activity"}

    # 1. 중복 이벤트 검증
    last_sync_time = RECENT_SYNC_REQUESTS.get(user_id, 0)
    if now_ts - last_sync_time < settings.DUPLICATE_WEBHOOK_DEBOUNCE_SECONDS:
        return {"status": "ok", "message": "duplicate_event_ignored"}
    
    RECENT_SYNC_REQUESTS[user_id] = now_ts

    try:
        if activity_type in ("installationUpdate", "conversationUpdate", "message"):
            # 2. ORM 객체를 세션에 다 올리지 않고 필요한 필드만 select() 실행하여 메모리 경량화
            stmt_user = select(User.id, User.email, User.grade).where(User.id == user_id)
            existing_user_row = db.execute(stmt_user).first()

            if not existing_user_row:
                # 신규 유저 생성
                new_user = User(
                    id=user_id,
                    email=user_email,
                    grade=user_grade,
                    conversation_id=user_conversation_id,
                    service_url=service_url,
                    is_active=True,
                    last_active_at=now_utc
                )
                db.add(new_user)
                logger.info(f"✨ [신규 유저 등록] User({user_id}) | Email: {user_email} | Grade: {user_grade}")
            else:
                # 기존 유저 정보 갱신 (지정 객체만 단건 조작)
                db_user = db.get(User, user_id)
                if db_user:
                    if user_email:
                        db_user.email = user_email
                    if user_grade:
                        db_user.grade = user_grade
                    if user_conversation_id:
                        db_user.conversation_id = user_conversation_id
                    if service_url:
                        db_user.service_url = service_url
                    db_user.is_active = True
                    db_user.last_active_at = now_utc
                    logger.info(f"🔄 [유저 정보 갱신] User({user_id}) | Email: {user_email} | Grade: {user_grade}")

            db.commit()

            # 3. 웰컴 메시지 발송
            if activity_type in ("installationUpdate", "conversationUpdate"):
                # 봇 추가/설치 시: 웰컴 메시지 발송
                welcome_text = (
                    "**CalendarSync 서비스가 정상 연결되었습니다!**\n\n"
                    "백그라운드에서 공지사항 및 포스터를 분석하여 "
                    "캘린더로 자동 동기화해 드립니다. 별도의 명령어 없이 작동합니다."
                )
                background_tasks.add_task(
                    bot_service.send_teams_reply,
                    service_url,
                    user_conversation_id,
                    welcome_text,
                )

            elif activity_type == "message":
                # 사용자가 일반 메시지를 보냈을 때: 안내 답장 메시지 발송
                reply_text = (
                    "**CalendarSync 자동 동기화 엔진 안내**\n\n"
                    "이 봇은 백그라운드 자동 동기화 전용 서비스입니다.\n"
                    "채널 공지사항 및 일정은 설정된 주기에 따라 자동 동기화됩니다."
                )
                background_tasks.add_task(
                    bot_service.send_teams_reply,
                    service_url,
                    user_conversation_id,
                    reply_text,
                )

            return {"status": "ok"}

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Teams Webhook 처리 중 에러 발생: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}