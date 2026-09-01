import logging
import hashlib
import asyncio
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from app.models.domain import User, UserChannelMapping
from app.models.master_calendar import MasterCalendar, UserSyncLog
from app.schemas.llm_schema import ScheduleAction, RAGAnalysisResult
from app.services.graph_service import GraphService

logger = logging.getLogger(__name__)


class SyncService:
    def __init__(self, graph_service: GraphService):
        self.graph = graph_service

    def _generate_content_hash(self, action: ScheduleAction) -> str:
        """일정 중복 및 변경 검증용 SHA-256 해시 생성"""
        raw_str = f"{action.title}|{action.start_datetime}|{action.end_datetime}|{action.location}|{action.description}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    async def process_rag_actions(
        self,
        db: Session,
        channel_id: str,
        raw_message_id: str,
        rag_result: RAGAnalysisResult
    ):
        """RAG 분석 결과(C/U/D)를 순회하며 MasterCalendar DB 변경 및 Fan-out 실행"""
        
        # 1. 메모리 최적화: ORM 전체 객체 대신 (user_id, grade) 튜플 리스트만 쿼리
        channel_users = (
            db.query(User.id, User.grade)
            .join(UserChannelMapping, User.id == UserChannelMapping.user_id)
            .filter(UserChannelMapping.channel_id == channel_id, User.is_active == True)
            .all()
        )

        for action in rag_result.actions:
            if action.action == "SKIP":
                logger.info(f"[SKIP] 사유: {action.reason}")
                continue

            elif action.action == "CREATE":
                await self._handle_create(db, channel_id, raw_message_id, action, channel_users)

            elif action.action == "UPDATE":
                await self._handle_update(db, action, channel_users)

            elif action.action == "DELETE":
                await self._handle_delete(db, action)

    # ------------------------------------------------------------------
    # [CREATE] 단일 유저 캘린더 생성 비동기 처리
    # ------------------------------------------------------------------
    async def _create_single_user_event(
        self,
        user_id: str,
        user_grade: Optional[str],
        master_item_id: str,
        title: str,
        start_dt: datetime,
        end_dt: datetime,
        location: Optional[str],
        description: Optional[str],
        target_grades: List[str]
    ) -> Optional[UserSyncLog]:
        # 대상 학년 필터링
        if target_grades and user_grade not in target_grades:
            return None 
        
        try:
            # MS Graph API 호출 (메모리상에서 복호화된 plain text 전달)
            outlook_event_id = await self.graph.create_user_calendar_event(
                user_id=user_id,
                title=title,
                start_dt=start_dt,
                end_dt=end_dt,
                location=location,
                description=description
            )

            # DB 등록용 SyncLog 객체 생성 및 반환
            return UserSyncLog(
                user_id=user_id,
                master_schedule_id=master_item_id,
                outlook_event_id=outlook_event_id
            )
        except Exception as e:
            logger.error(f"User {user_id} 캘린더 CREATE Fan-out 실패: {e}")
            return None

    async def _handle_create(
        self,
        db: Session,
        channel_id: str,
        raw_message_id: str,
        action: ScheduleAction,
        target_users: List[tuple]  # [(user_id, grade), ...]
    ):
        """[CREATE] MasterCalendar 생성 (자동 암호화) -> asyncio.gather 병렬 Fan-out"""
        content_hash = self._generate_content_hash(action)

        # 1. MasterCalendar 생성 (@property를 통해 title, location, description 자동 암호화 저장됨)
        master_item = MasterCalendar(
            source_channel_id=channel_id,
            raw_message_id=raw_message_id,
            title=action.title,
            start_datetime=datetime.fromisoformat(action.start_datetime),
            end_datetime=datetime.fromisoformat(action.end_datetime),
            location=action.location,
            description=action.description,
            target_grades=action.target_grades,
            content_hash=content_hash
        )
        db.add(master_item)
        db.flush()  # master_item.id 채번

        logger.info(f"[CREATE MasterCalendar] ID: {master_item.id}")

        # 2. asyncio.gather용 파라미터 값 추출 (메모리 복호화된 값 전달)
        master_id = master_item.id
        title = master_item.title
        start_dt = master_item.start_datetime
        end_dt = master_item.end_datetime
        location = master_item.location
        description = master_item.description

        # 3. asyncio.gather로 병렬 API 호출 실행 (DB 세션 객체 전달 안함 -> Thread/Async Safe)
        tasks = [
            self._create_single_user_event(
                u_id, u_grade, master_id, title, start_dt, end_dt, location, description, action.target_grades
            )
            for u_id, u_grade in target_users
        ]
        results = await asyncio.gather(*tasks)

        # 4. 성공한 로그들 메인 스레드 DB 세션에서 일괄 저장
        valid_logs = [log for log in results if log is not None]
        if valid_logs:
            db.add_all(valid_logs)
        db.commit()
        db.expunge_all()  # 세션 캐시 초기화 (Stash 방지)

    # ------------------------------------------------------------------
    # [UPDATE] 단일 유저 캘린더 수정/신규생성 비동기 처리
    # ------------------------------------------------------------------
    async def _update_single_user_event(
        self,
        user_id: str,
        user_grade: Optional[str],
        master_id: str,
        existing_event_id: Optional[str],
        title: str,
        start_dt: datetime,
        end_dt: datetime,
        location: Optional[str],
        description: Optional[str],
        target_grades: List[str]
    ) -> Optional[UserSyncLog]:
        is_target_grade = not target_grades or (user_grade in target_grades)

        if is_target_grade:
            if existing_event_id:
                # 기존 유저 -> PATCH (수정)
                try:
                    await self.graph.update_user_calendar_event(
                        user_id=user_id,
                        event_id=existing_event_id,
                        title=title,
                        start_dt=start_dt,
                        end_dt=end_dt,
                        location=location,
                        description=description
                    )
                except Exception as e:
                    logger.error(f"User {user_id} 캘린더 UPDATE Fan-out 실패: {e}")
                return None
            else:
                # 학년 변경 등으로 새로 대상이 된 유저 -> POST (신규 생성)
                try:
                    outlook_event_id = await self.graph.create_user_calendar_event(
                        user_id=user_id,
                        title=title,
                        start_dt=start_dt,
                        end_dt=end_dt,
                        location=location,
                        description=description
                    )
                    return UserSyncLog(
                        user_id=user_id,
                        master_schedule_id=master_id,
                        outlook_event_id=outlook_event_id
                    )
                except Exception as e:
                    logger.error(f"User {user_id} 캘린더 신규 등록 Fan-out 실패: {e}")
                    return None
        return None

    async def _handle_update(self, db: Session, action: ScheduleAction, target_users: List[tuple]):
        """[UPDATE] 마스터 일정 수정 (자동 암호화) -> 핀포인트 병렬 Update/Insert"""
        if not action.master_schedule_id:
            logger.warning("[UPDATE] master_schedule_id 누락으로 스킵")
            return

        master_item = db.query(MasterCalendar).get(action.master_schedule_id)
        if not master_item:
            logger.error(f"[UPDATE] ID {action.master_schedule_id} 마스터 일정을 찾을 수 없음")
            return

        # 1. MasterCalendar 정보 업데이트 (@property를 통해 자동 암호화 저장됨)
        master_item.title = action.title
        master_item.start_datetime = datetime.fromisoformat(action.start_datetime)
        master_item.end_datetime = datetime.fromisoformat(action.end_datetime)
        master_item.location = action.location
        master_item.description = action.description
        master_item.target_grades = action.target_grades
        master_item.content_hash = self._generate_content_hash(action)

        # 2. 기존 SyncLog 맵핑 (user_id -> outlook_event_id)
        existing_logs = (
            db.query(UserSyncLog.user_id, UserSyncLog.outlook_event_id)
            .filter(UserSyncLog.master_schedule_id == master_item.id)
            .all()
        )
        log_map = {u_id: evt_id for u_id, evt_id in existing_logs}

        # 3. asyncio.gather 병렬 수정 처리
        tasks = [
            self._update_single_user_event(
                u_id, u_grade, master_item.id, log_map.get(u_id),
                master_item.title, master_item.start_datetime, master_item.end_datetime,
                master_item.location, master_item.description, action.target_grades
            )
            for u_id, u_grade in target_users
        ]
        results = await asyncio.gather(*tasks)

        # 4. 새로 생성된 UserSyncLog만 추가 저장
        new_logs = [log for log in results if log is not None]
        if new_logs:
            db.add_all(new_logs)

        db.commit()
        db.expunge_all()

    # ------------------------------------------------------------------
    # [DELETE] 마스터 일정 삭제 및 병렬 삭제 Fan-out
    # ------------------------------------------------------------------
    async def _delete_single_user_event(self, user_id: str, outlook_event_id: str):
        try:
            await self.graph.delete_user_calendar_event(
                user_id=user_id,
                event_id=outlook_event_id
            )
        except Exception as e:
            logger.error(f"User {user_id} 캘린더 DELETE Fan-out 실패: {e}")

    async def _handle_delete(self, db: Session, action: ScheduleAction):
        """[DELETE] 마스터 일정 및 핀포인트 유저 Outlook 이벤트 병렬 삭제"""
        if not action.master_schedule_id:
            logger.warning("[DELETE] master_schedule_id 누락으로 스킵")
            return

        master_item = db.query(MasterCalendar).get(action.master_schedule_id)
        if not master_item:
            logger.error(f"[DELETE] ID {action.master_schedule_id} 마스터 일정을 찾을 수 없음")
            return

        # 1. 기존 동기화 로그 전체 조회 (user_id, outlook_event_id)
        sync_logs = (
            db.query(UserSyncLog.user_id, UserSyncLog.outlook_event_id)
            .filter(UserSyncLog.master_schedule_id == master_item.id)
            .all()
        )

        # 2. asyncio.gather로 Graph API DELETE 호출 병렬화
        tasks = [
            self._delete_single_user_event(u_id, evt_id)
            for u_id, evt_id in sync_logs if evt_id
        ]
        if tasks:
            await asyncio.gather(*tasks)

        # 3. MasterCalendar DB 삭제 (CASCADE 설정으로 UserSyncLog도 자동 삭제됨)
        db.delete(master_item)
        db.commit()
        db.expunge_all()
        logger.info(f"[DELETE MasterCalendar 완료] ID: {action.master_schedule_id}")