import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.domain import User
from app.models.master_calendar import UserSyncLog

logger = logging.getLogger(__name__)

async def run_lifecycle_cleanup_task():
    """[주 주기 실행] 휴면 계정 비활성화 및 오래된 로그 정리"""
    db: Session = SessionLocal()
    try:
        now = datetime.utcnow()

        # 1. 90일 이상 미활동 유저 비활성화 (휴면 처리)
        inactive_threshold = now - timedelta(days=90)
        dormant_users = (
            db.query(User)
            .filter(User.is_active == True, User.last_active_at < inactive_threshold)
            .all()
        )
        for user in dormant_users:
            user.is_active = False
            logger.info(f"[Lifecycle] 휴면 계정 전환: {user.id}")

        # 2. 180일 이상 지난 오래된 UserSyncLog 삭제 (DB 용량 최적화)
        log_cleanup_threshold = now - timedelta(days=180)
        deleted_count = (
            db.query(UserSyncLog)
            .filter(UserSyncLog.synced_at < log_cleanup_threshold)
            .delete(synchronize_session=False)
        )

        db.commit()
        logger.info(f"✅ 라이프사이클 태스크 완료 (휴면 전환: {len(dormant_users)}명 | 만료 로그 삭제: {deleted_count}건)")
    except Exception as e:
        db.rollback()
        logger.error(f"❌ 라이프사이클 태스크 중 에러: {e}")
    finally:
        db.close()