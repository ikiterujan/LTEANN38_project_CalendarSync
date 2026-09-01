import logging
import hashlib
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
import asyncio

from app.models.domain import User, Channel, UserChannelMapping
from app.models.master_calendar import MasterCalendar, UserSyncLog
from app.schemas.llm_schema import ScheduleAction, RAGAnalysisResult
# MS Graph API 연동 클라이언트 (기존 Graph API 호출 모듈)
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
        
        # 해당 채널 구독 유저 및 학년 정보 조회
        channel_users: List[User] = (
            db.query(User)
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
                await self._handle_delete(db, action, channel_users)

    # ------------------------------------------------------------------
    # C/U/D 세부 처리 함수들 (MasterDB C/U/D -> Fan-out)
    # ------------------------------------------------------------------

    async def _create_single_user_event(self,db : Session, user: User, master_item : MasterCalendar, action : ScheduleAction):
        #Fan-out (대상 학년 유저 필터링 후 MS Graph API 호출)
        if action.target_grades and user.grade not in action.target_grades:
            return None  # 대상 학년이 아니면 스킵
        
        try:
            # MS Graph API로 유저 개인 캘린더에 일정 생성
            outlook_event_id = await self.graph.create_user_calendar_event(
                user_id=user.id,
                title=master_item.title,
                start_dt=master_item.start_datetime,
                end_dt=master_item.end_datetime,
                location=master_item.location,
                description=master_item.description
            )

            # 동기화 로그 기록
            sync_log = UserSyncLog(
                user_id=user.id,
                master_schedule_id=master_item.id,
                outlook_event_id=outlook_event_id
            )
            db.add(sync_log)
        except Exception as e:
            logger.error(f"User {user.id} 캘린더 CREATE Fan-out 실패: {e}")
                        
    async def _handle_create(
        self,
        db: Session,
        channel_id: str,
        raw_message_id: str,
        action: ScheduleAction,
        target_users: List[User]
    ):
        """[CREATE] 마스터 일정 생성 -> 대상 학년 유저들 Outlook 캘린더에 Fan-out 추가"""
        content_hash = self._generate_content_hash(action)

        # 1. MasterCalendar 생성
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
        db.flush()  # master_item.id 생성

        logger.info(f"[CREATE MasterCalendar] ID: {master_item.id} | 제목: {master_item.title}")

        tasks = [
            self._create_single_user_event(db, user, master_item, action)
            for user in target_users
        ]
        results = await asyncio.gather(*tasks)

        # 3. 성공한 SyncLog들만 한 번에 DB에 bulk insert
        valid_logs = [log for log in results if log is not None]
        if valid_logs:
            db.add_all(valid_logs)
        db.commit()

    async def _update_sing_user_event(self, db: Session, user: User, master_item: MasterCalendar, action: ScheduleAction):
        existing_logs = db.query(UserSyncLog).filter_by(master_schedule_id=master_item.id).all()
        log_map = {log.user_id: log for log in existing_logs}
        is_target_grade = not action.target_grades or (user.grade in action.target_grades)

        if is_target_grade:
            if user.id in log_map and log_map[user.id].outlook_event_id:
                # 기존에 등록된 유저 -> Graph API PATCH (수정)
                try:
                    await self.graph.update_user_calendar_event(
                        user_id=user.id,
                        event_id=log_map[user.id].outlook_event_id,
                        title=master_item.title,
                        start_dt=master_item.start_datetime,
                        end_dt=master_item.end_datetime,
                        location=master_item.location,
                        description=master_item.description
                    )
                except Exception as e:
                    logger.error(f"User {user.id} 캘린더 UPDATE Fan-out 실패: {e}")
            else:
                # 업데이트로 새로 대상에 포함된 유저 -> Graph API POST (신규 생성)
                try:
                    outlook_event_id = await self.graph.create_user_calendar_event(
                        user_id=user.id,
                        title=master_item.title,
                        start_dt=master_item.start_datetime,
                        end_dt=master_item.end_datetime,
                        location=master_item.location,
                        description=master_item.description
                    )
                    db.add(UserSyncLog(user_id=user.id, master_schedule_id=master_item.id, outlook_event_id=outlook_event_id))
                except Exception as e:
                    logger.error(f"User {user.id} 캘린더 신규 등록 Fan-out 실패: {e}")
                    
    async def _handle_update(self, db: Session, action: ScheduleAction, target_users: List[User]):
        """[UPDATE] 마스터 일정 수정 -> 핀포인트로 해당 유저들의 Outlook 이벤트 수정"""
        if not action.master_schedule_id:
            logger.warning("[UPDATE] master_schedule_id 누락으로 스킵")
            return

        master_item = db.query(MasterCalendar).get(action.master_schedule_id)
        if not master_item:
            logger.error(f"[UPDATE] ID {action.master_schedule_id}에 해당하는 마스터 일정을 찾을 수 없음")
            return

        # 1. MasterCalendar 정보 업데이트
        master_item.title = action.title
        master_item.start_datetime = datetime.fromisoformat(action.start_datetime)
        master_item.end_datetime = datetime.fromisoformat(action.end_datetime)
        master_item.location = action.location
        master_item.description = action.description
        master_item.target_grades = action.target_grades
        master_item.content_hash = self._generate_content_hash(action)

        # 2. 기존 UserSyncLog 및 Graph API Fan-out Update
        existing_logs = db.query(UserSyncLog).filter_by(master_schedule_id=master_item.id).all()
        log_map = {log.user_id: log for log in existing_logs}

        tasks = [
            self._update_single_user_event(db, user, master_item, action)
            for user in target_users
        ]
        results = await asyncio.gather(*tasks)
        
        valid_logs = [log for log in results if log is not None]
        if valid_logs:
            db.add_all(valid_logs)
        db.commit()

    async def _handle_delete(self, db: Session, action: ScheduleAction, target_users: List[User]):
        """[DELETE] 마스터 일정 및 핀포인트 유저 Outlook 이벤트 삭제"""
        if not action.master_schedule_id:
            logger.warning("[DELETE] master_schedule_id 누락으로 스킵")
            return

        master_item = db.query(MasterCalendar).get(action.master_schedule_id)
        if not master_item:
            logger.error(f"[DELETE] ID {action.master_schedule_id}에 해당하는 마스터 일정을 찾을 수 없음")
            return

        # 1. 등록되어 있던 모든 유저의 Outlook 이벤트 삭제
        sync_logs = db.query(UserSyncLog).filter_by(master_schedule_id=master_item.id).all()
        for log in sync_logs:
            if log.outlook_event_id:
                try:
                    await self.graph.delete_user_calendar_event(
                        user_id=log.user_id,
                        event_id=log.outlook_event_id
                    )
                except Exception as e:
                    logger.error(f"User {log.user_id} 캘린더 DELETE Fan-out 실패: {e}")

        # 2. MasterCalendar DB Record 삭제 (CASCADE로 UserSyncLog 자동 삭제)
        db.delete(master_item)
        db.commit()
        logger.info(f"[DELETE MasterCalendar 완료] ID: {action.master_schedule_id}")