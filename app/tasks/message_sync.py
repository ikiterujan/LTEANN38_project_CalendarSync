#app/tasks/message_sync.py
import logging
import asyncio
from typing import List, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.dependencies import graph_service, llm_service, sync_service
from app.services.sync_service import SyncService
from app.models.domain import Channel
from app.core.timezone import now_kst

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

async def get_calculated_lookback_minutes(db: Session, channel_id: str) -> int:
    stmt = select(Channel).where(Channel.channel_id == channel_id)
    channel = db.execute(stmt).scalars().first()

    # 1. 최초 동기화인 경우
    if not channel or channel.last_synced_at is None:
        lookback_days = settings.MESSAGE_SYNC_INITIAL_LOOKBACK_DAYS
        return lookback_days * 24 * 60

    # 2. 다운타임 대응: 실제 현재 시각과 last_synced_at의 차이(분) 계산 + 넉넉한 버퍼(15분)
    now = now_kst()
    time_diff = now - channel.last_synced_at
    calculated_minutes = int(time_diff.total_seconds() / 60) + settings.MESSAGE_SYNC_LOOKBACK_BUFFER_MINUTES

    # 최소 1시간(Periodic 기본값) 보장
    min_minutes = settings.MESSAGE_SYNC_INTERVAL_HOURS * 60 + settings.MESSAGE_SYNC_LOOKBACK_BUFFER_MINUTES
    
    return max(calculated_minutes, min_minutes)

async def _process_single_channel_messages(channel_id: str, team_id: str):
    """
    단일 채널 메시지 수집 및 RAG C/U/D 파이프라인 실행
    (독립된 Short-Lived DB Session을 사용하여 Thread/Async Safe 보장)
    """
    # 병렬 태스크별 독립 세션 생성 (세션 충돌 및 메모리 Stash 완벽 방지)
    with SessionLocal() as db:
        try:
            lookback_minutes = await get_calculated_lookback_minutes(db, channel_id)
            messages: List[Dict[str, Any]] = await graph_service.get_channel_messages(
                team_id=team_id,
                channel_id=channel_id,
                since_minutes=lookback_minutes
            )

            for msg in messages:
                content = await sync_service.extract_message_content(msg_payload=msg)
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
                    team_id=team_id,
                    channel_id=channel_id,
                    raw_message_id=msg["id"],
                    rag_result=rag_result
                )
            # 1. select 구문으로 채널 조회
            stmt = select(Channel).where(Channel.channel_id == channel_id)
            channel = db.execute(stmt).scalar_one_or_none()

            if channel:
                channel.last_synced_at = now_kst()
            else:
                # 채널 레코드가 없다면 신규 생성
                channel = Channel(
                    id=channel_id,
                    team_id=team_id,
                    last_synced_at=now_kst()
                )
                db.add(channel)

            db.commit()  # 최종 커밋
            '''
            logger.info(f"✅ 채널 {channel_id} 동기화 완료 및 last_synced_at 업데이트 성공")
            '''

        except Exception as e:
            '''
            logger.error(f"Channel {channel_id} 메시지 동기화 에러: {e}", exc_info=True)
            '''
            logger.error(f"Channel 메시지 동기화 에러: {e}", exc_info=True)


async def sync_channel_messages_task():
    """[메시지 동기화 태스크] 채널별 독립 세션 기반 asyncio.gather 병렬 처리"""
    
    # 1. 대상 채널 목록만 빠르게 조회하고 세션 즉시 닫기
    try:
        with SessionLocal() as db:
            stmt = (
                select(Channel.channel_id, Channel.team_id)
                .order_by(Channel.last_synced_at.asc().nullsfirst())
            )
            channels: List[Tuple[str, str]] = db.execute(stmt).all()
            # with 블록을 빠져나오며 세션(커넥션)이 DB 풀로 안전하게 반환됨
            
        if not channels:
            logger.info("동기화 대상 채널이 없습니다.")
            return

        total_channels = len(channels)
        total_batches = (total_channels + settings.MESSAGE_SYNC_BATCH_SIZE - 1) // settings.MESSAGE_SYNC_BATCH_SIZE
        
        # 2. DB 세션이 "없는" 상태에서 마음 편히 느린 작업(LLM, Graph API, 대기) 수행
        for i in range(0, total_channels, settings.MESSAGE_SYNC_BATCH_SIZE):
            batch = channels[i : i + settings.MESSAGE_SYNC_BATCH_SIZE]
            current_batch_num = (i // settings.MESSAGE_SYNC_BATCH_SIZE) + 1
            
            logger.info(f"[Batch {current_batch_num}/{total_batches}] {len(batch)}개 채널 동기화 중...")
            
            tasks = [
                _process_single_channel_messages(ch_id, team_id)
                for ch_id, team_id in batch
            ]
            
            # 여기서 아무리 오래 걸려도 바깥에는 유지 중인 DB 세션이 없으므로 안전함
            await asyncio.gather(*tasks, return_exceptions=True)
            
            if i + settings.MESSAGE_SYNC_BATCH_SIZE < total_channels:
                await asyncio.sleep(settings.BATCH_DELAY_SECONDS)
                
        logger.info("메시지 및 일정 동기화 태스크 완료 (병렬 처리)")

    except Exception as e:
        logger.error(f"메시지 동기화 태스크 실행 중 에러: {e}", exc_info=True)