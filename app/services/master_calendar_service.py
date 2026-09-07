import hashlib
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from sqlalchemy.orm import Session

from app.core.dependencies import graph_service, llm_service
from app.models.master_calendar import MasterCalendar
from app.core.config import settings

logger = logging.getLogger(__name__)


class MasterCalendarService:
    """GPT 분석 결과 기반 Master Calendar 일정 관리 서비스"""

    def _generate_content_hash(self, text: str, channel_id: str) -> str:
        """메시지 내용 기반 SHA-256 해시 생성 (중복 수집 방지용)"""
        raw_bytes = f"{channel_id}:{text.strip()}".encode("utf-8")
        return hashlib.sha256(raw_bytes).hexdigest()

    async def process_and_create_master_event(
        self,
        text_content: str,
        raw_message_id: str,
        channel_id: str,
        team_id: str,
        image_urls: Optional[List[str]] = None,
        db: Session = None
    ) -> Optional[str]:
        """
        1. 중복 검증 (content_hash)
        2. LLM(GPT) 공지/포스터 분석
        3. MS Graph API로 Master Calendar에 일정 등록
        4. DB MasterCalendar 저장
        """
        try:
            # 0. 중복 체크
            computed_hash = self._generate_content_hash(text_content, channel_id)
            existing_event = db.query(MasterCalendar).filter(
                MasterCalendar.content_hash == computed_hash
            ).first()

            if existing_event:
                logger.info(f"ℹ️ 이미 동기화된 공지사항입니다. (Hash: {computed_hash[:10]}...)")
                return existing_event.id

            # 1. GPT API 멀티모달 분석
            parsed_data = await llm_service.analyze_notice_with_gpt(
                text=text_content,
                image_urls=image_urls
            )

            if not parsed_data or not parsed_data.get("is_event"):
                logger.info("ℹ️ 일정이 포함되지 않은 공지사항입니다.")
                return None

            # 필드 정제
            title = parsed_data.get("title") or parsed_data.get("subject", "제목 없음")
            description = parsed_data.get("description", text_content)
            location = parsed_data.get("location")
            target_grades = parsed_data.get("target_grades", [])

            # 2. MS Graph API Master Calendar에 일정 등록
            master_user_id = settings.MASTER_CALENDAR_USER_ID
            
            event_payload = {
                "subject": title,
                "body": {
                    "contentType": "HTML",
                    "content": f"<p>{description}</p><br><p><b>[자동 동기화된 채널 일정]</b></p>"
                },
                "start": {
                    "dateTime": parsed_data["start_datetime"],
                    "timeZone": "Asia/Seoul"
                },
                "end": {
                    "dateTime": parsed_data["end_datetime"],
                    "timeZone": "Asia/Seoul"
                },
                "location": {
                    "displayName": location if location else "온라인 / 장소 미정"
                },
                "singleValueExtendedProperties": [
                    {
                        "id": "String {fbd4ec16-7f0f-46d2-a9db-f32258a48607} Name CalendarSyncApp",
                        "value": "CalendarSync_2026"
                    }
                ]
            }

            created_event = await graph_service.create_master_event(
                user_id=master_user_id,
                event_data=event_payload
            )

            master_event_id = created_event.get("id")

            # 3. DB MasterCalendar 레코드 생성 (EncryptedString 자동 암호화 적용)
            master_event = MasterCalendar(
                id=master_event_id,  # Graph API Event ID를 DB PK로 매핑 (필요시 uuid로 변경 가능)
                source_channel_id=channel_id,
                raw_message_id=raw_message_id,
                title=title,
                location=location,
                description=description,
                start_datetime=parsed_data["start_datetime"],
                end_datetime=parsed_data["end_datetime"],
                grade1=1 in target_grades or "1" in target_grades,
                grade2=2 in target_grades or "2" in target_grades,
                grade3=3 in target_grades or "3" in target_grades,
                content_hash=computed_hash
            )

            db.add(master_event)
            db.commit()

            logger.info(f"✅ Master Calendar 일정 DB 저장 완료: {title} (ID: {master_event_id})")
            return master_event_id

        except Exception as e:
            db.rollback()
            logger.error(f"❌ Master Calendar 일정 처리 실패: {e}", exc_info=True)
            return None


master_calendar_service = MasterCalendarService()