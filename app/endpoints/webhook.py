# app/endpoints/webhook.py
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict

from fastapi import APIRouter, Request, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.domain import User
from app.services.graph_service import GraphService
from app.core.config import settings
from app.core.dependencies import graph_service

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# APIRouter 생성
router = APIRouter(tags=["Webhook"])

RECENT_SYNC_REQUESTS: Dict[str, float] = {}

@router.post("/api/messages")
async def teams_event_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Teams Bot Framework 이벤트 수신 및 유저 자동 등록/업데이트"""
    data = await request.json()
    activity_type = data.get("type")
    now_utc = datetime.now(timezone.utc)
    now_ts = now_utc.timestamp()

    from_user = data.get("from", {})
    user_id = from_user.get("aadObjectId") or from_user.get("id")
    user_conversation = data.get("conversation") or {}
    user_conversation_id = user_conversation.get("id")
    service_url = data.get("serviceUrl")

    if not user_id:
        return {"status": "ok", "message": "no_user_id_in_activity"}

    # 1. 중복 Webhook 이벤트 데바운스 검증
    last_sync_time = RECENT_SYNC_REQUESTS.get(user_id, 0)
    if now_ts - last_sync_time < settings.DUPLICATE_WEBHOOK_DEBOUNCE_SECONDS:
        return {"status": "ok", "message": "duplicate_event_ignored"}
    
    RECENT_SYNC_REQUESTS[user_id] = now_ts

    try:
        # 2. User DB 자동 등록 및 업데이트
        if activity_type in ("installationUpdate", "conversationUpdate", "message"):
            existing_user = db.query(User).filter(User.id == user_id).first()

            if not existing_user:
                new_user = User(
                    id=user_id,
                    conversation_id=user_conversation_id,
                    service_url=service_url,
                    is_active=True
                )
                db.add(new_user)
                logger.info(f"✨ [신규 유저 등록] User({user_id})")
            else:
                if user_conversation_id:
                    existing_user.conversation_id = user_conversation_id
                if service_url:
                    existing_user.service_url = service_url
                existing_user.is_active = True
                logger.info(f"🔄 [유저 정보 갱신] User({user_id})")

            db.commit()

            welcome_text = (
                "**CalendarSync 서비스가 정상 연결되었습니다!**\n\n"
                "백그라운드에서 공지사항 및 포스터를 분석하여 "
                "캘린더로 자동 동기화해 드립니다. 별도의 명령어 없이 작동합니다."
            )
            background_tasks.add_task(graph_service.send_teams_chat_message, user_conversation_id, service_url, welcome_text)

        elif activity_type == "message":

            reply_text = (
                "**CalendarSync 자동 동기화 엔진 안내**\n\n"
                "이 봇은 백그라운드 자동 동기화 전용 서비스입니다.\n"
                "채널 공지사항 및 일정은 설정된 주기에 따라 자동 동기화됩니다."
            )
            background_tasks.add_task(
                background_tasks.add_task(graph_service.send_teams_chat_message, user_conversation_id, service_url, welcome_text)
            )

        return {"status": "ok"}

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Teams Webhook 처리 중 에러 발생: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}