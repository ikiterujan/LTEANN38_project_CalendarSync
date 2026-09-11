import logging
import hashlib
import asyncio
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import Dict, Any, List, Tuple
from bs4 import BeautifulSoup
import json
import httpx
import base64
import pytesseract
from PIL import Image
import io

from app.models.domain import User, UserChannelMapping
from app.models.master_calendar import MasterCalendar, UserSyncLog
from app.schemas.llm_schema import ScheduleAction, RAGAnalysisResult
from app.services.graph_service import GraphService
from app.services.ocr_service import EasyOCRService
from app.utils.teams import build_teams_message_link

logger = logging.getLogger(__name__)


def _is_target_user(user_grade: Optional[str], target_grades: List[int]) -> bool:
    if not target_grades:
        return True
    if not user_grade:
        return False
    try:
        return int(user_grade) in target_grades
    except ValueError:
        return False


def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str or not dt_str.strip():
        return None
    try:
        return datetime.fromisoformat(dt_str)
    except ValueError:
        logger.warning(f"잘못된 날짜 포맷: '{dt_str}'")
        return None


def _format_title(subject: Optional[str], title: str) -> str:
    """[방법 B] [Subject] Title 포맷팅"""
    if subject and subject.strip():
        clean_subj = subject.strip().replace("[", "").replace("]", "")
        return f"[{clean_subj}] {title.strip()}"
    return title.strip()


def _build_full_description(description: Optional[str], teams_link: Optional[str]) -> str:
    """본문 하단에 Teams 원본 메시지 링크 결합"""
    desc_parts = []
    if description and description.strip():
        formatted_desc = description.strip().replace("\n", "<br>")
        desc_parts.append(f"<div>{formatted_desc}</div>")
    
    if teams_link:
        link_html = (
            f'<br><br><a href="{teams_link}" target="_blank" '
            f'style="font-weight: bold; color: #005A9E; text-decoration: underline;">'
            f'Teams 원본 게시물 바로가기</a>'
        )
        desc_parts.append(link_html)
        
    return "".join(desc_parts)

def _split_long_term_actions(actions: List[ScheduleAction]) -> List[ScheduleAction]:
    """
    8일 이상 지속되는 장기 일정을 [#시작], [#종료] 2개의 ScheduleAction 객체로 분할합니다.
    """
    processed_actions = []

    for action in actions:
        # CREATE 동작이 아니거나, 날짜가 없는 경우 그대로 유지
        if action.action != "CREATE" or not action.start_datetime or not action.end_datetime:
            processed_actions.append(action)
            continue

        start_dt = _parse_datetime(action.start_datetime)
        end_dt = _parse_datetime(action.end_datetime)

        if not start_dt or not end_dt:
            processed_actions.append(action)
            continue

        # 날짜 차이 계산 (일 단위)
        day_diff = (end_dt.date() - start_dt.date()).days

        # 8일 이상 지속되는 장기 기간 일정인 경우 2개로 분할
        if day_diff >= 8:
            # 1) 시작일 당일 객체 (#시작)
            start_action = action.model_copy(deep=True)
            start_action.title = f"{action.title} [#시작]"
            start_action.start_datetime = f"{start_dt.strftime('%Y-%m-%d')}T00:00:00"
            start_action.end_datetime = f"{start_dt.strftime('%Y-%m-%d')}T23:59:00"
            processed_actions.append(start_action)

            # 2) 종료일 당일 객체 (#종료)
            end_action = action.model_copy(deep=True)
            end_action.title = f"{action.title} [#종료]"
            end_action.start_datetime = f"{end_dt.strftime('%Y-%m-%d')}T00:00:00"
            end_action.end_datetime = f"{end_dt.strftime('%Y-%m-%d')}T23:59:00"
            processed_actions.append(end_action)
        else:
            # 7일 이하 단기/당일 일정은 그대로 유지
            processed_actions.append(action)

    return processed_actions

class SyncService:
    def __init__(self, graph_service: GraphService, ocr_service: EasyOCRService):
        self.graph = graph_service
        self.ocr_service = ocr_service

    def _generate_content_hash(self, formatted_title: str, action: ScheduleAction) -> str:
        raw_str = f"{formatted_title}|{action.start_datetime}|{action.end_datetime}|{action.location}|{action.description}"
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()

    async def process_rag_actions(
        self,
        db: Session,
        team_id: str,
        channel_id: str,
        raw_message_id: str,
        rag_result: RAGAnalysisResult
    ):
        stmt = (
            select(User.id, User.grade)
            .join(UserChannelMapping, User.id == UserChannelMapping.user_id)
            .where(
                UserChannelMapping.channel_id == channel_id,
                User.is_active == True
            )
        )
        channel_users = db.execute(stmt).all()

        # Teams Deep Link 생성
        teams_link = build_teams_message_link(team_id, channel_id, raw_message_id)

        actions_to_process = _split_long_term_actions(rag_result.actions)
        
        for action in actions_to_process:
            if action.action == "SKIP":
                logger.info(f"[SKIP] 사유: {action.reason}")
                continue

            elif action.action == "CREATE":
                await self._handle_create(
                    db, channel_id, raw_message_id, teams_link, action, channel_users
                )

            elif action.action == "UPDATE":
                await self._handle_update(db, teams_link, action, channel_users)

            elif action.action == "DELETE":
                await self._handle_delete(db, action)

    # ------------------------------------------------------------------
    # [CREATE]
    # ------------------------------------------------------------------
    async def _create_single_user_event(
        self,
        user_id: str,
        user_grade: Optional[str],
        master_item_id: str,
        formatted_title: str,
        start_dt: datetime,
        end_dt: datetime,
        location: Optional[str],
        full_description: Optional[str],
        target_grades: List[int]
    ) -> Optional[UserSyncLog]:
        if not _is_target_user(user_grade, target_grades):
            return None 
        
        try:
            outlook_event_id = await self.graph.create_user_calendar_event(
                user_id=user_id,
                title=formatted_title,
                start_dt=start_dt,
                end_dt=end_dt,
                location=location,
                description=full_description
            )

            return UserSyncLog(
                user_id=user_id,
                master_schedule_id=master_item_id,
                outlook_event_id=outlook_event_id
            )
        except Exception as e:
            '''
            logger.error(f"User {user_id} 캘린더 CREATE Fan-out 실패: {e}")
            '''
            logger.error(f"캘린더 CREATE Fan-out 실패: {e}")
            return None

    async def _handle_create(
        self,
        db: Session,
        channel_id: str,
        raw_message_id: str,
        teams_link: str,
        action: ScheduleAction,
        target_users: List[tuple]
    ):
        start_dt = _parse_datetime(action.start_datetime)
        end_dt = _parse_datetime(action.end_datetime)

        if not start_dt or not end_dt:
            logger.warning(f"[CREATE SKIP] 유효하지 않은 날짜 (start: '{action.start_datetime}', end: '{action.end_datetime}')")
            return

        formatted_title = action.title.strip()
        full_description = _build_full_description(action.description, teams_link)
        content_hash = self._generate_content_hash(formatted_title, action)

        stmt = select(MasterCalendar.id).where(
            MasterCalendar.source_channel_id == channel_id,
            MasterCalendar.content_hash == content_hash
        )
        duplicate_id = db.execute(stmt).scalar_one_or_none()

        if duplicate_id:
            logger.info(f"[CREATE SKIP] 중복 일정 존재 (ID: {duplicate_id})")
            return

        master_item = MasterCalendar(
            source_channel_id=channel_id,
            raw_message_id=raw_message_id,
            title=formatted_title,
            start_datetime=start_dt,
            end_datetime=end_dt,
            location=action.location,
            description=full_description,
            grade1=(1 in action.target_grades),
            grade2=(2 in action.target_grades),
            grade3=(3 in action.target_grades),
            content_hash=content_hash
        )
        
        db.add(master_item)
        db.flush()

        tasks = [
            self._create_single_user_event(
                u_id, u_grade, master_item.id, formatted_title, start_dt, end_dt,
                action.location, full_description, action.target_grades
            )
            for u_id, u_grade in target_users
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_logs = [log for log in results if isinstance(log, UserSyncLog)]
        if valid_logs:
            db.add_all(valid_logs)
        db.commit()
        db.expunge_all()

    # ------------------------------------------------------------------
    # [UPDATE]
    # ------------------------------------------------------------------
    async def _update_single_user_event(
        self,
        user_id: str,
        user_grade: Optional[str],
        master_id: str,
        existing_event_id: Optional[str],
        formatted_title: str,
        start_dt: datetime,
        end_dt: datetime,
        location: Optional[str],
        full_description: Optional[str],
        target_grades: List[int]
    ) -> Optional[UserSyncLog]:
        if _is_target_user(user_grade, target_grades):
            if existing_event_id:
                try:
                    await self.graph.update_user_calendar_event(
                        user_id=user_id,
                        event_id=existing_event_id,
                        title=formatted_title,
                        start_dt=start_dt,
                        end_dt=end_dt,
                        location=location,
                        description=full_description
                    )
                except Exception as e:
                    '''
                    logger.error(f"User {user_id} 캘린더 UPDATE Fan-out 실패: {e}")
                    '''
                    logger.error(f"캘린더 UPDATE Fan-out 실패: {e}")
                return None
            else:
                try:
                    outlook_event_id = await self.graph.create_user_calendar_event(
                        user_id=user_id,
                        title=formatted_title,
                        start_dt=start_dt,
                        end_dt=end_dt,
                        location=location,
                        description=full_description
                    )
                    return UserSyncLog(
                        user_id=user_id,
                        master_schedule_id=master_id,
                        outlook_event_id=outlook_event_id
                    )
                except Exception as e:
                    '''
                    logger.error(f"User {user_id} 캘린더 신규 등록 Fan-out 실패: {e}")
                    '''
                    logger.error(f"캘린더 신규 등록 Fan-out 실패: {e}")
                    return None
        return None

    async def _handle_update(
        self,
        db: Session,
        teams_link: str,
        action: ScheduleAction,
        target_users: List[tuple]
    ):
        if not action.master_schedule_id:
            logger.warning("[UPDATE] master_schedule_id 누락 스킵")
            return

        master_item = db.get(MasterCalendar, action.master_schedule_id)
        if not master_item:
            logger.error(f"[UPDATE] 기존 마스터 일정 없음")
            return

        start_dt = _parse_datetime(action.start_datetime)
        end_dt = _parse_datetime(action.end_datetime)

        if not start_dt or not end_dt:
            logger.warning(f"[UPDATE SKIP] 유효하지 않은 날짜")
            return

        formatted_title = _format_title(action.subject, action.title)
        full_description = _build_full_description(action.description, teams_link)

        master_item.title = formatted_title
        master_item.start_datetime = start_dt
        master_item.end_datetime = end_dt
        master_item.location = action.location
        master_item.description = full_description
        master_item.grade1 = (1 in action.target_grades)
        master_item.grade2 = (2 in action.target_grades)
        master_item.grade3 = (3 in action.target_grades)
        master_item.content_hash = self._generate_content_hash(formatted_title, action)

        stmt = select(UserSyncLog.user_id, UserSyncLog.outlook_event_id).where(
            UserSyncLog.master_schedule_id == master_item.id
        )
        existing_logs = db.execute(stmt).all()
        log_map = {u_id: evt_id for u_id, evt_id in existing_logs}

        tasks = [
            self._update_single_user_event(
                u_id, u_grade, master_item.id, log_map.get(u_id),
                formatted_title, start_dt, end_dt, action.location,
                full_description, action.target_grades
            )
            for u_id, u_grade in target_users
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        new_logs = [log for log in results if isinstance(log, UserSyncLog)]
        if new_logs:
            db.add_all(new_logs)

        db.commit()
        db.expunge_all()

    # ------------------------------------------------------------------
    # [DELETE]
    # ------------------------------------------------------------------
    async def _delete_single_user_event(self, user_id: str, outlook_event_id: str):
        try:
            await self.graph.delete_user_calendar_event(
                user_id=user_id,
                event_id=outlook_event_id
            )
        except Exception as e:
            '''
            logger.error(f"User {user_id} 캘린더 DELETE Fan-out 실패: {e}")
            '''
            logger.error(f"캘린더 DELETE Fan-out 실패: {e}")

    async def _handle_delete(self, db: Session, action: ScheduleAction):
        if not action.master_schedule_id:
            logger.warning("[DELETE] master_schedule_id 누락 스킵")
            return

        master_item = db.get(MasterCalendar, action.master_schedule_id)
        if not master_item:
            logger.error(f"[DELETE] 기존 마스터 일정 없음")
            return

        stmt = select(UserSyncLog.user_id, UserSyncLog.outlook_event_id).where(
            UserSyncLog.master_schedule_id == master_item.id
        )
        sync_logs = db.execute(stmt).all()

        tasks = [
            self._delete_single_user_event(u_id, evt_id)
            for u_id, evt_id in sync_logs if evt_id
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        db.delete(master_item)
        db.commit()
        db.expunge_all()
        
    
    async def extract_message_content(
        self,
        msg_payload: Dict[str, Any],
    ) -> Tuple[str, bool]:
        """
        MS Teams 메시지 Payload에서 텍스트(본문+공지배너)와 이미지(OCR 텍스트)를 추출하여
        단일 텍스트 프롬프트 문자열로 변환합니다.
        """
        raw_content = msg_payload.get("body", {}).get("content", "")
        content_type = msg_payload.get("body", {}).get("contentType", "text")

        ocr_texts: List[str] = []
        clean_body = raw_content

        # 1. HTML 본문 파싱 및 인라인 이미지 OCR 텍스트 추출
        if content_type.lower() == "html" and raw_content:
            soup = BeautifulSoup(raw_content, "html.parser")
            try:
                if self.graph._access_token:
                    img_tags = soup.find_all("img")
                    headers = {"Authorization": f"Bearer {self.graph._access_token}"}
                    
                    for img in img_tags:
                        img_url = img.get("src")
                        if img_url and "hostedContents" in img_url:
                            try:
                                res = await self.graph._client.get(img_url, headers=headers, follow_redirects=True)
                                if res.status_code == 200:
                                    # EasyOCRService를 사용하여 bytes에서 바로 텍스트 추출
                                    extracted_text = self.ocr_service.extract_text(res.content)
                                    
                                    if extracted_text:
                                        ocr_texts.append(f"[첨부 이미지 내 텍스트]:\n{extracted_text}")
                            except Exception as e:
                                logger.warning(f"이미지 다운로드 및 OCR 실패 ({img_url}): {e}")

                clean_body = soup.get_text(separator=" ", strip=True)
            finally:
                soup.decompose()

        # 2. 첨부파일(공지 배너 등) 텍스트 추출
        attachment_texts: List[str] = []
        for att in msg_payload.get("attachments", []):
            att_content = att.get("content")
            if not att_content:
                continue

            if isinstance(att_content, str):
                try:
                    parsed_att = json.loads(att_content)
                    if isinstance(parsed_att, dict) and "title" in parsed_att:
                        attachment_texts.append(f"[공지 배너]: {parsed_att['title']}")
                    else:
                        attachment_texts.append(att_content)
                except json.JSONDecodeError:
                    attachment_texts.append(att_content)
            elif isinstance(att_content, dict) and "title" in att_content:
                attachment_texts.append(f"[공지 배너]: {att_content['title']}")

        # 3. 본문 + 첨부 텍스트 + OCR 추출 텍스트 병합
        full_text_parts = []
        if clean_body:
            full_text_parts.append(clean_body)
        if attachment_texts:
            full_text_parts.append("\n".join(attachment_texts))
        if ocr_texts:
            full_text_parts.append("\n".join(ocr_texts))

        final_text_prompt = "\n\n".join(full_text_parts)
        has_images = len(ocr_texts) > 0

        # 텍스트가 아예 없으면 빈 값 반환
        if not final_text_prompt.strip():
            return "", False

        return final_text_prompt, has_images