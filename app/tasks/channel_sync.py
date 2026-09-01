import logging
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.core.dependencies import graph_service
from app.models.domain import User, Channel, UserChannelMapping

logger = logging.getLogger(__name__)

async def sync_user_channels_task():
    """유저별 Teams 채널 목록 동기화 (4시간 주기)"""
    db: Session = SessionLocal()
    try:
        users = db.query(User).filter(User.is_active == True).all()
        for user in users:
            # Graph API로 유저 가입 채널 조회
            joined_channels = await graph_service.get_user_joined_channels(user.id)
            
            for ch in joined_channels:
                # 1. Channel 테이블 Upsert
                channel_obj = db.query(Channel).filter_by(channel_id=ch["id"]).first()
                if not channel_obj:
                    channel_obj = Channel(
                        channel_id=ch["id"],
                        team_id=ch["team_id"],
                        channel_name=ch.get("displayName")
                    )
                    db.add(channel_obj)
                    db.flush()

                # 2. UserChannelMapping 매핑
                mapping = db.query(UserChannelMapping).filter_by(
                    user_id=user.id, channel_id=ch["id"]
                ).first()
                if not mapping:
                    db.add(UserChannelMapping(user_id=user.id, channel_id=ch["id"]))
            
            db.commit()
        logger.info("✅ 채널 동기화 태스크 완료")
    except Exception as e:
        db.rollback()
        logger.error(f"❌ 채널 동기화 중 에러: {e}")
    finally:
        db.close()