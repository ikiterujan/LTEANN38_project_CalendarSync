import logging
import asyncio
from typing import List, Tuple, Dict, Any
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.dependencies import graph_service, llm_service
from app.services.sync_service import SyncService
from app.models.domain import Channel

logger = logging.getLogger(__name__)
sync_service = SyncService(graph_service)


async def _process_single_channel_messages(channel_id: str, team_id: str):
    """
    단일 채널 메시지 수집 및 RAG C/U/D 파이프라인 실행
    (독립된 Short-Lived DB Session을 사용하여 Thread/Async Safe 보장)
    """
    # 병렬 태스크별 독립 세션 생성 (세션 충돌 및 메모리 Stash 완벽 방지)
    db: Session = SessionLocal()
    try:
        messages: List[Dict[str, Any]] = await graph_service.get_channel_messages(
            team_id=team_id,
            channel_id=channel_id
        )

        for msg in messages:
            content = msg.get("body", {}).get("content")
            if not content:
                continue

            # 1. LLM RAG 분석 (내부에서 필드 튜플 조회 및 db.expunge_all 실행됨)
            rag_result = await llm_service.analyze_message_with_rag(
                db=db,
                channel_id=channel_id,
                message_text=content
            )

            # 2. MasterCalendar DB 반영 및 Fan-out 실행
            await sync_service.process_rag_actions(
                db=db,
                channel_id=channel_id,
                raw_message_id=msg["id"],
                rag_result=rag_result
            )

    except Exception as e:
        logger.error(f"Channel {channel_id} 메시지 동기화 에러: {e}", exc_info=True)
    finally:
        db.close()  # 작업 완료 후 즉시 세션 닫기


async def sync_channel_messages_task():
    """[메시지 동기화 태스크] 채널별 독립 세션 기반 asyncio.gather 병렬 처리"""
    db: Session = SessionLocal()
    try:
        # 1. ORM 객체 전체 대신 필요 필드만 튜플로 스칼라 쿼리 (메모리 경량화)
        channels: List[Tuple[str, str]] = (
            db.query(Channel.channel_id, Channel.team_id).all()
        )

        if not channels:
            logger.info("ℹ️ 동기화 대상 채널이 없습니다.")
            return

        # 2. Primitive 값(channel_id, team_id)만 전달하여 병렬 실행
        tasks = [
            _process_single_channel_messages(ch_id, team_id)
            for ch_id, team_id in channels
        ]
        
        await asyncio.gather(*tasks)
        logger.info("✅ 메시지 및 일정 동기화 태스크 완료 (병렬 처리)")

    except Exception as e:
        logger.error(f"❌ 메시지 동기화 태스크 실행 중 에러: {e}", exc_info=True)
    finally:
        db.close()