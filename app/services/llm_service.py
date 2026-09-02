import json
import logging
from typing import List, Optional
from sqlalchemy.orm import Session
from openai import AsyncOpenAI

from app.models.master_calendar import MasterCalendar
from app.schemas.master_calendar import MasterScheduleContext
from app.schemas.llm_schema import RAGAnalysisResult
from app.core.security import decrypt_text  # 복호화 함수 추가
from app.core.timezone import now_kst

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self, openai_client: AsyncOpenAI):
        self.client = openai_client

    def _get_existing_schedules_context(
        self, db: Session, channel_id: str
    ) -> List[dict]:
        """[RAG Retrieval] ORM 메모리 최적화 + 복호화를 거쳐 Context용 JSON 리스트 생성"""
        
        # ORM 객체 매핑 대신 튜플 단위 핀포인트 조회 (메모리 Stash 방지)
        schedules = (
            db.query(
                MasterCalendar.id,
                MasterCalendar._title,
                MasterCalendar.start_datetime,
                MasterCalendar.end_datetime,
                MasterCalendar._location,
                MasterCalendar._description,
                MasterCalendar.target_grades,
            )
            .filter(MasterCalendar.source_channel_id == channel_id)
            .all()
        )

        context_list = []
        for s_id, _title, start_dt, end_dt, _loc, _desc, target_grades in schedules:
            context_obj = MasterScheduleContext(
                id=s_id,
                title=decrypt_text(_title),               # 메모리상에서 복호화
                start_datetime=start_dt.isoformat(),
                end_datetime=end_dt.isoformat(),
                location=decrypt_text(_loc) if _loc else None,  # 메모리상에서 복호화
                description=decrypt_text(_desc) if _desc else None, # 메모리상에서 복호화
                target_grades=target_grades or [],
            )
            context_list.append(context_obj.model_dump())

        db.expunge_all()  # 세션 캐시 즉시 비우기
        return context_list

    async def analyze_message_with_rag(
        self,
        db: Session,
        channel_id: str,
        message_text: str,
        current_year: Optional[int] = None,
    ) -> RAGAnalysisResult:
        """[RAG Generation] 기존 일정 Context + 새 메시지를 GPT-4o-mini로 전달하여 C/U/D 판단"""
        if current_year is None:
            current_year = now_kst().year

        # 1. RAG Context 추출 (안전한 복호화 텍스트 반환)
        existing_schedules = self._get_existing_schedules_context(db, channel_id)
        context_json_str = json.dumps(
            existing_schedules, ensure_ascii=False, indent=2
        )

        # 2. RAG System Prompt 작성
        system_prompt = f"""
너는 대학 및 조직의 공지사항을 분석하여 마스터 캘린더를 최신 상태로 유지하는 AI 엔진이다.
기준 연도는 {current_year}년이다.

아래 주어진 [기존 마스터 일정 목록]과 [새로 수신된 공지글]을 정밀 비교하여 수행해야 할 C/U/D 액션(CREATE, UPDATE, DELETE, SKIP)을 판단하라.

[기존 마스터 일정 목록]
{context_json_str if existing_schedules else "현재 등록된 기존 일정 없음"}

[C/U/D 판단 및 작성 규칙]
1. **CREATE**: 기존 일정 목록에 없는 완전히 새로운 일정인 경우.
2. **UPDATE**: 기존 일정과 동일 대상/목적의 행사인데 날짜, 시간, 장소, 내용 등이 변경, 연기, 수정된 경우.
   - **중요**: UPDATE 판정 시 반드시 [기존 마스터 일정 목록]의 해당 `id` 값을 `master_schedule_id` 필드에 명시해야 한다.
3. **DELETE**: 기존에 등록된 일정이 "취소되었다", "폐지되었다", "실시하지 않는다"는 내용이 명시된 경우.
   - **중요**: DELETE 판정 시 반드시 해당 `id` 값을 `master_schedule_id` 필드에 명시해야 한다.
4. **SKIP**: 이미 정확히 동일한 내용으로 등록되어 있거나, 일시/장소 등 캘린더 등록에 유의미한 정보가 없는 단순 안내글인 경우.
5. 날짜 형식은 반드시 ISO 8601 (`YYYY-MM-DDTHH:MM:SS`) 규격을 준수하라.
"""

        try:
            # 3. GPT-4o-mini Structured Output 호출
            response = await self.client.beta.chat.completions.parse(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": f"새로 수신된 공지글 내용:\n{message_text}",
                    },
                ],
                response_format=RAGAnalysisResult,
                temperature=0.0,  # 결정론적 판단을 위해 0으로 고정
                seed=42,
            )

            result: RAGAnalysisResult = response.choices[0].message.parsed
            logger.info(
                f"[{channel_id}] RAG 분석 완료 - 추출된 액션 수: {len(result.actions)}개"
            )
            return result

        except Exception as e:
            logger.error(f"LLM RAG 분석 중 오류 발생: {e}", exc_info=True)
            raise e