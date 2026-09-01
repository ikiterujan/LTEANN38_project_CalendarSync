import logging
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.core.dependencies import graph_service, llm_service
from app.services.sync_service import SyncService
from app.models.domain import Channel

logger = logging.getLogger(__name__)
sync_service = SyncService(graph_service)

async def sync_channel_messages_task():
    """채널 메시지 수집 및 RAG C/U/D 파이프라인 실행 (1시간 주기)"""
    db: Session = SessionLocal()
    try:
        channels = db.query(Channel).all()
        for channel in channels:
            # Graph API로 채널의 recent 메시지 가져오기
            messages = await graph_service.get_channel_messages(
                team_id=channel.team_id, 
                channel_id=channel.channel_id
            )
            
            for msg in messages:
                if not msg.get("body", {}).get("content"):
                    continue
                
                # LLM RAG C/U/D 판별
                rag_result = await llm_service.analyze_message_with_rag(
                    db=db,
                    channel_id=channel.channel_id,
                    message_text=msg["body"]["content"]
                )
                
                # Master DB 수정 및 유저 캘린더 Fan-out
                await sync_service.process_rag_actions(
                    db=db,
                    channel_id=channel.channel_id,
                    raw_message_id=msg["id"],
                    rag_result=rag_result
                )
                
        logger.info("✅ 메시지 및 일정 동기화 태스크 완료")
    except Exception as e:
        logger.error(f"❌ 메시지 동기화 중 에러: {e}")
    finally:
        db.close()