# app/tasks/master_sync.py
import logging
from app.core.database import SessionLocal
from app.models.domain import Channel
from app.core.dependencies import graph_service
from app.services.master_calendar_service import master_calendar_service

logger = logging.getLogger(__name__)


async def sync_master_calendar_task():
    """등록된 모든 채널의 최신 공지를 수집하여 GPT 분석 및 Master Calendar 일정 생성"""
    logger.info("🔄 [Master Sync Task] 채널 공지 수집 및 마스터 캘린더 동기화 시작")
    
    with SessionLocal() as db:
        try:
            # 1. DB에 등록된 활성 채널 목록 조회 (스칼라 Row 추출)
            channels = db.query(Channel.team_id, Channel.channel_id).all()
            
            if not channels:
                logger.info("ℹ️ [Master Sync Task] 동기화할 채널이 없습니다.")
                return

            for team_id, channel_id in channels:
                try:
                    # 2. Graph API로 채널의 최신 메시지/공지 가져오기 (delta)
                    messages = await graph_service.get_channel_messages(team_id, channel_id)
                    if not messages:
                        continue

                    for msg in messages:
                        message_id = msg.get("id")
                        text_content = msg.get("body", {}).get("content", "")
                        
                        # 첨부된 이미지/포스터 URL 추출 (있는 경우)
                        attachments = msg.get("attachments", [])
                        image_urls = [
                            att.get("contentUrl") for att in attachments 
                            if att.get("contentType", "").startswith("image/") and att.get("contentUrl")
                        ]

                        if not text_content and not image_urls:
                            continue

                        # 3. Master Calendar 생성 서비스 호출 (중복 검증 -> GPT 분석 -> Master Calendar 등록)
                        await master_calendar_service.process_and_create_master_event(
                            text_content=text_content,
                            raw_message_id=message_id,
                            channel_id=channel_id,
                            team_id=team_id,
                            image_urls=image_urls,
                            db=db
                        )

                except Exception as ch_err:
                    logger.error(f"⚠️ [Master Sync Task] 채널({channel_id}) 수집 중 에러: {ch_err}")

            db.expunge_all()
            logger.info("✅ [Master Sync Task] 마스터 캘린더 동기화 완료")

        except Exception as e:
            db.rollback()
            logger.error(f"❌ [Master Sync Task] 실행 중 에러 발생: {e}", exc_info=True)