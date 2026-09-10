#app/services/llm_service.py
import json
import logging
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select
from openai import AsyncOpenAI

from app.models.master_calendar import MasterCalendar
from app.schemas.master_calendar import MasterScheduleContext
from app.schemas.llm_schema import RAGAnalysisResult
from app.core.timezone import now_kst

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class LLMService:
    def __init__(self, openai_client: AsyncOpenAI):
        self.client = openai_client

    def _get_existing_schedules_context(
        self, db: Session, channel_id: str
    ) -> List[dict]:
        """[RAG Retrieval] ORM 메모리 Overhead 없이 핀포인트 select 프로젝션 수행.
        EncryptedString에 의해 title, location, description은 이미 자동으로 복호화된 상태입니다.
        """
        
        # 1. ORM 객체 매핑 대신 핀포인트 select 프로젝션 (EncryptedString 자동 복호화 적용됨)
        stmt = select(
            MasterCalendar.id,
            MasterCalendar.title,
            MasterCalendar.start_datetime,
            MasterCalendar.end_datetime,
            MasterCalendar.location,
            MasterCalendar.description,
            MasterCalendar.grade1,
            MasterCalendar.grade2,
            MasterCalendar.grade3,
        ).where(MasterCalendar.source_channel_id == channel_id)

        rows = db.execute(stmt).mappings().all()

        context_list = []
        for row in rows:
            # datetime 필드 isoformat 변환 처리 및 Pydantic 매핑
            row_dict = dict(row)
            target_grades = []
            if row_dict.pop("grade1", False): target_grades.append(1)
            if row_dict.pop("grade2", False): target_grades.append(2)
            if row_dict.pop("grade3", False): target_grades.append(3)
            row_dict["target_grades"] = target_grades
            if row_dict.get("start_datetime"):
                row_dict["start_datetime"] = row_dict["start_datetime"].isoformat()
            if row_dict.get("end_datetime"):
                row_dict["end_datetime"] = row_dict["end_datetime"].isoformat()

            # MasterScheduleContext (pydantic v2 model_config 적용됨) 변환
            context_obj = MasterScheduleContext.model_validate(row_dict)
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

        # 1. RAG Context 추출 (자동 복호화된 평문 텍스트 반환)
        existing_schedules = self._get_existing_schedules_context(db, channel_id)
        context_json_str = json.dumps(
            existing_schedules, ensure_ascii=False, indent=2
        )

        # 2. RAG System Prompt 작성
        system_prompt = f"""너는 대학 및 학사 공지사항을 분석하여 마스터 캘린더를 최신 상태로 관리하는 고성능 AI 도우미다.
기준 연도는 {current_year}년이다.

주어진 [기존 마스터 일정 목록]과 [새로 수신된 공지 메세지]를 정밀 비교하여 정확한 C/U/D 액션(CREATE, UPDATE, DELETE, SKIP)을 추출하라.

=========================================
[1. 기본 추출 및 포맷팅 규칙]
=========================================
1. 제목(title) 기본 형태: `[과목명/카테고리] 핵심 내용`
   - 본문에 과목명/행사명이 포함되면 최우선 카테고리로 지정 (예: [물리학] 1차 과제 제출)
2. 학년(target_grades): 한국 학교 기준 숫자로만 구성 (1, 2, 3 등). 전교생/공통인 경우 빈 리스트 `[]`.
3. 본문에 구체적 날짜 언급이 없으면 작성일로 임의 할당 절대 금지.
4. 마감 공지 처리:
   - 시작일 없이 마감일만 존재하는 경우 ("~까지 제출"):
     * start_datetime: 마감일 당일 00:00:00
     * end_datetime: 지정 시각 (미지정 시 23:59:59)
     * title: 제목 끝에 `#마감` 태그 필수 부착 (예: [장학] 현송장학금 신청#마감)

5.[학교 표준 교시-시간 매핑 테이블]
입력 텍스트에 "N교시" 표현이 있는 경우, 아래 시각을 기준으로 start_time과 end_time을 자동으로 계산하여 입력해라.
- 1교시: 08:30 ~ 09:20
- 2교시: 09:30 ~ 10:20
- 3교시: 10:30 ~ 11:20
- 4교시: 11:30 ~ 12:20
- 점심시간: 12:20 ~ 13:10
- 5교시: 13:10 ~ 14:00
- 6교시: 14:10 ~ 15:00
- 7교시: 15:10 ~ 16:00

=========================================
[2. RAG C/U/D 판정 로직]
=========================================
1. CREATE:
   - 기존 목록에 없는 완전 신규 일정.
   - 단, 구체적 연/월/일/시간 마감이 없는 단순 안내문(식당 메뉴, 도서관 운영 등)은 SKIP 처리.
2. UPDATE:
   - 기존 일정과 동일 대상/목적인데 날짜, 시간, 장소, 내용이 변경/연기/수정된 경우.
   - [중요] 기존 목록의 해당 `id`를 `master_schedule_id`에 반드시 기재.
3. DELETE:
   - 기존 일정이 "취소", "폐지", "실시하지 않음"으로 명시된 경우.
   - [중요] 기존 목록의 해당 `id`를 `master_schedule_id`에 반드시 기재.
4. SKIP:
   - 이미 완전히 동일한 일정 및 시간으로 등록되어 있는 경우.
   - 구체적인 날짜나 일시가 언급되지 않은 단순 공지글.

=========================================
[3. FEW-SHOT EXAMPLES (판단 예시)]
=========================================
[예시 1 - UPDATE (일정 연기)]
- 기존 일정: {{"id": "uuid-123", "title": "[물리학] 중간고사", "start_datetime": "{current_year}-10-10T10:00:00"}}
- 새 공지: "물리학 중간고사가 10월 10일에서 10월 12일 10시로 연기되었습니다."
- 결과:
  action: UPDATE, master_schedule_id: "uuid-123", title: "[물리학] 중간고사", start_datetime: "{current_year}-10-12T10:00:00"

[예시 2 - DELETE (일정 취소)]
- 기존 일정: {{"id": "uuid-456", "title": "[특강] AI 세미나", "start_datetime": "{current_year}-11-01T14:00:00"}}
- 새 공지: "금주 예정이었던 AI 세미나 특강은 강사 사정으로 취소되었습니다."
- 결과:
  action: DELETE, master_schedule_id: "uuid-456"


=========================================
[기존 마스터 일정 목록]
{context_json_str if existing_schedules else "현재 등록된 기존 일정 없음"}
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
            '''
            logger.info(
                f"[{channel_id}] RAG 분석 완료 - 추출된 액션 수: {len(result.actions)}개"
            )
            '''
            return result

        except Exception as e:
            logger.error(f"LLM RAG 분석 중 오류 발생: {e}", exc_info=True)
            raise e