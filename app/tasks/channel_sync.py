#app/tasks/channel_sync.py
import logging
import asyncio
from typing import List, Tuple, Dict, Any, Set
from sqlalchemy.orm import Session
from sqlalchemy import select, update

from app.core.database import SessionLocal
from app.core.dependencies import graph_service
from app.models.domain import User, Channel, UserChannelMapping

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


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
    
    with SessionLocal() as db:
        try:
            # 1. ORM 인스턴스 대신 user_id(문자열)만 스칼라 쿼리 (메모리 Stash 차단)
            stmt_users = select(User.id).where(User.is_active == True)
            active_user_ids = db.execute(stmt_users).scalars().all()

            if not active_user_ids:
                logger.info("ℹ️ 동기화할 활성 유저가 없습니다.")
                return

            # 2. asyncio.gather로 Graph API 요청 병렬 실행 (순수 user_id만 전달)
            tasks = [_sync_single_user_channels(u_id) for u_id in active_user_ids]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
            results: List[Tuple[str, List[Dict[str, Any]]]] = [
                r for r in raw_results if not isinstance(r, BaseException)
            ]

            # 3. 기존 DB 데이터 한 번에 조회하여 Set/Dict 캐싱 (N+1 쿼리 완전 제거)
            # (1) 채널 목록 캐싱: {channel_id: {"team_id": ..., "channel_name": ...}}
            stmt_channels = select(Channel.channel_id, Channel.team_id, Channel.channel_name)
            existing_channels: Dict[str, dict] = {
                row.channel_id: {"team_id": row.team_id, "channel_name": row.channel_name}
                for row in db.execute(stmt_channels)
            }

            # (2) N:M 매핑 목록 캐싱: {(user_id, channel_id), ...}
            stmt_mappings = select(UserChannelMapping.user_id, UserChannelMapping.channel_id)
            existing_mappings: Set[Tuple[str, str]] = set(db.execute(stmt_mappings).all())

            new_channels_dict: Dict[str, Channel] = {}
            updated_channels: List[dict] = []  # 이름 변경 채널 업데이트용
            new_mappings: List[UserChannelMapping] = []

            # 4. 메모리 내 셋 검증 및 Bulk 객체 준비
            for user_id, joined_channels in results:
                for ch in joined_channels:
                    ch_id = ch["id"]
                    team_id = ch["team_id"]
                    ch_name = ch.get("displayName")

                    # [케이스 A] 신규 채널 등록
                    if ch_id not in existing_channels and ch_id not in new_channels_dict:
                        new_channels_dict[ch_id] = Channel(
                            channel_id=ch_id,
                            team_id=team_id,
                            channel_name=ch_name
                        )

                    # [케이스 B] 기존 채널 이름 변경 감지
                    elif ch_id in existing_channels:
                        old_name = existing_channels[ch_id]["channel_name"]
                        if ch_name and old_name != ch_name:
                            updated_channels.append({"channel_id": ch_id, "channel_name": ch_name})
                            existing_channels[ch_id]["channel_name"] = ch_name  # 메모리 캐시 갱신

                    # [케이스 C] N:M 매핑 검증 및 추가
                    mapping_key = (user_id, ch_id)
                    if mapping_key not in existing_mappings:
                        new_mappings.append(UserChannelMapping(user_id=user_id, channel_id=ch_id))
                        existing_mappings.add(mapping_key)  # 중복 추가 방지

            # 5. Bulk DB 반영
            # (1) 신규 채널 일괄 추가
            if new_channels_dict:
                db.add_all(list(new_channels_dict.values()))
                db.flush()

            # (2) 이름 변경된 채널 Bulk Update
            if updated_channels:
                for item in updated_channels:
                    db.execute(
                        update(Channel)
                        .where(Channel.channel_id == item["channel_id"])
                        .values(channel_name=item["channel_name"])
                    )

            # (3) 신규 유저-채널 매핑 일괄 추가
            if new_mappings:
                db.add_all(new_mappings)

            db.commit()
            db.expunge_all()  # 세션 캐시 즉시 비우기 (메모리 Stash 차단)
            
            logger.info(
                f"✅ 채널 동기화 완료 "
                f"(신규 채널: {len(new_channels_dict)}개, "
                f"이름 변경: {len(updated_channels)}개, "
                f"신규 매핑: {len(new_mappings)}개)"
            )

        except Exception as e:
            db.rollback()
            logger.error(f"❌ 채널 동기화 중 에러 발생: {e}", exc_info=True)