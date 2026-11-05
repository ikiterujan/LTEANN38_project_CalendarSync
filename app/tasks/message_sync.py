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

    # 2. 타임존 동기화 처리 (Offset-aware / Naive 변환 방어 로직)
    now = now_kst()
    last_synced = channel.last_synced_at

    # DB에서 온 last_synced_at에 tzinfo가 없다면 now의 tzinfo(KST)를 부여
    if last_synced.tzinfo is None:
        last_synced = last_synced.replace(tzinfo=now.tzinfo)

    # 3. 시간 차이 계산 (분 단위) + 버퍼 추가
    time_diff = now - last_synced
    calculated_minutes = int(time_diff.total_seconds() / 60) + settings.MESSAGE_SYNC_LOOKBACK_BUFFER_MINUTES

    # 최소 periodic 기본 인터벌 보장
    min_minutes = settings.MESSAGE_SYNC_INTERVAL_HOURS * 60 + settings.MESSAGE_SYNC_LOOKBACK_BUFFER_MINUTES

    return max(calculated_minutes, min_minutes)

async def _process_single_channel_messages(channel_id: str, team_id: str):
    """
    단일 채널 메시지 수집 및 RAG C/U/D 파이프라인 실행
    (외부 I/O 호출 구간에서는 DB 세션을 생성하지 않아 커넥션 풀 고갈을 방지)
    """
    try:
        # 1. [DB 세션 1] Lookback 계산용 단발성 조회 후 즉시 세션 닫기
        with SessionLocal() as db:
            lookback_minutes = await get_calculated_lookback_minutes(db, channel_id)

        # 2. [외부 Network I/O] Graph API 메시지 수집 (DB 세션 미점유)
        messages: List[Dict[str, Any]] = await graph_service.get_channel_messages(
            team_id=team_id,
            channel_id=channel_id,
            since_minutes=lookback_minutes
        )

        if not messages:
            # 메시지가 없어도 last_synced_at은 갱신
            with SessionLocal() as db:
                _update_channel_last_synced(db, channel_id, team_id)
            return

        # 3. 메시지별 RAG 분석 및 DB 저장
        for msg in messages:
            content = await sync_service.extract_message_content(msg_payload=msg)
            if not content:
                continue

            # 3-1. [DB 세션 2] LLM RAG 분석용 Context 조회 및 LLM 호출
            with SessionLocal() as db:
                rag_result = await llm_service.analyze_message_with_rag(
                    db=db,
                    channel_id=channel_id,
                    message_text=content
                )

            # 3-2. [DB 세션 3] MasterCalendar 저장 및 Fan-out 실행
            with SessionLocal() as db:
                await sync_service.process_rag_actions(
                    db=db,
                    team_id=team_id,
                    channel_id=channel_id,
                    raw_message_id=msg["id"],
                    rag_result=rag_result
                )

        # 4. [DB 세션 4] 최종 동기화 시각 업데이트
        with SessionLocal() as db:
            _update_channel_last_synced(db, channel_id, team_id)

    except Exception as e:
        logger.error(f"Channel {channel_id} 메시지 동기화 에러: {e}", exc_info=True)


def _update_channel_last_synced(db: Session, channel_id: str, team_id: str):
    """채널 sync 시간 업데이트 헬퍼 함수"""
    stmt = select(Channel).where(Channel.channel_id == channel_id)
    channel = db.execute(stmt).scalar_one_or_none()

    if channel:
        channel.last_synced_at = now_kst()
    else:
        channel = Channel(
            channel_id=channel_id,
            team_id=team_id,
            last_synced_at=now_kst()
        )
        db.add(channel)
    db.commit()


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