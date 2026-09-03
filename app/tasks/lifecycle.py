#app/tasks/lifecycle.py
import logging
from datetime import timedelta
from sqlalchemy import update, delete
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.timezone import now_kst
from app.models.domain import User
from app.models.master_calendar import UserSyncLog

logger = logging.getLogger(__name__)


async def run_lifecycle_cleanup_task():
    """[주 주기 실행] 휴면 계정 비활성화 및 오래된 로그 정리"""
    
    with SessionLocal() as db:
        try:
            now = now_kst()

            # 1. 90일 이상 미활동 유저 일괄 비활성화 (Bulk UPDATE)
            inactive_threshold = now - timedelta(days=90)
            
            stmt_update_dormant = (
                update(User)
                .where(
                    User.is_active == True,
                    User.last_active_at < inactive_threshold
                )
                .values(is_active=False)
            )
            
            result_update = db.execute(stmt_update_dormant)
            dormant_count = result_update.rowcount

            # 2. 180일 이상 지난 오래된 UserSyncLog 일괄 삭제 (Bulk DELETE)
            log_cleanup_threshold = now - timedelta(days=180)
            
            stmt_delete_logs = (
                delete(UserSyncLog)
                .where(UserSyncLog.synced_at < log_cleanup_threshold)
            )
            
            result_delete = db.execute(stmt_delete_logs)
            deleted_log_count = result_delete.rowcount

            # 3. 트랜잭션 반영 및 커밋
            db.commit()
            
            logger.info(
                f"✅ 라이프사이클 태스크 완료 "
                f"(휴면 전환: {dormant_count}명 | 만료 로그 삭제: {deleted_log_count}건)"
            )

        except Exception as e:
            db.rollback()
            logger.error(f"❌ 라이프사이클 태스크 중 에러 발생: {e}", exc_info=True)