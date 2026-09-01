import logging
import asyncio
from typing import List, Tuple, Dict, Any
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.dependencies import graph_service
from app.models.domain import User, Channel, UserChannelMapping

logger = logging.getLogger(__name__)


async def _sync_single_user_channels(user_id: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    단일 유저의 채널 목록 가져오기 (DB Session 미전달 / Async Safe)
    """
    try:
        joined_channels = await graph_service.get_user_joined_channels(user_id)
        return user_id, joined_channels
    except Exception as e:
        logger.error(f"User {user_id} 채널 조회 실패: {e}")
        return user_id, []


async def sync_user_channels_task():
    """[채널 동기화 태스크] ORM 메모리 누수 방지 및 Bulk DB 반영"""
    db: Session = SessionLocal()
    try:
        # 1. ORM 인스턴스 대신 user_id(문자열)만 스칼라 쿼리 (메모리 Stash 차단)
        active_user_ids = [u[0] for u in db.query(User.id).filter(User.is_active == True).all()]

        if not active_user_ids:
            logger.info("ℹ️ 동기화할 활성 유저가 없습니다.")
            return

        # 2. asyncio.gather로 Graph API 요청 병렬 실행 (순수 user_id만 전달)
        tasks = [_sync_single_user_channels(u_id) for u_id in active_user_ids]
        results: List[Tuple[str, List[Dict[str, Any]]]] = await asyncio.gather(*tasks)

        # 3. 기존 DB 데이터 한 번에 조회하여 Set/Dict 캐싱 (N+1 쿼리 완전 제거)
        existing_channels: Dict[str, Channel] = {
            c.channel_id: c for c in db.query(Channel).all()
        }
        existing_mappings: set = {
            (m.user_id, m.channel_id) for m in db.query(UserChannelMapping.user_id, UserChannelMapping.channel_id).all()
        }

        new_channels_dict: Dict[str, Channel] = {}
        new_mappings: List[UserChannelMapping] = []

        # 4. 메모리 내 셋 검증 및 Bulk 객체 준비
        for user_id, joined_channels in results:
            for ch in joined_channels:
                ch_id = ch["id"]
                team_id = ch["team_id"]
                ch_name = ch.get("displayName")

                # 채널 등록 여부 검증
                if ch_id not in existing_channels and ch_id not in new_channels_dict:
                    new_channel = Channel(
                        channel_id=ch_id,
                        team_id=team_id,
                        channel_name=ch_name
                    )
                    new_channels_dict[ch_id] = new_channel

                # 유저-채널 매핑 여부 검증
                if (user_id, ch_id) not in existing_mappings:
                    new_mappings.append(UserChannelMapping(user_id=user_id, channel_id=ch_id))
                    existing_mappings.add((user_id, ch_id))  # 중복 추가 방지용 Set 업데이트

        # 5. Bulk Insert 일괄 적용
        if new_channels_dict:
            db.add_all(list(new_channels_dict.values()))
            db.flush()

        if new_mappings:
            db.add_all(new_mappings)

        db.commit()
        db.expunge_all()  # 세션 캐시 즉시 비우기 (메모리 Stash 차단)
        logger.info(f"✅ 채널 동기화 완료 (신규 채널: {len(new_channels_dict)}개, 신규 매핑: {len(new_mappings)}개)")

    except Exception as e:
        db.rollback()
        logger.error(f"❌ 채널 동기화 중 에러 발생: {e}", exc_info=True)
    finally:
        db.close()