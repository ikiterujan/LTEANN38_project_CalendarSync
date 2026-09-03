#app/schemas/llm_schema.py
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class ScheduleAction(BaseModel):
    action: Literal["CREATE", "UPDATE", "DELETE", "SKIP"] = Field(
        ..., 
        description="CREATE: 새 일정 추가, UPDATE: 기존 일정 수정, DELETE: 일정 취소, SKIP: 변경없음 또는 중복"
    )
    master_schedule_id: Optional[str] = Field(
        None, 
        description="UPDATE 또는 DELETE일 경우 대상 MasterCalendar ID (CREATE/SKIP일 경우 None)"
    )
    title: str = Field(..., description="일정 제목")
    start_datetime: str = Field(..., description="시작 일시 (ISO 8601 형식: YYYY-MM-DDTHH:MM:SS)")
    end_datetime: str = Field(..., description="종료 일시 (ISO 8601 형식: YYYY-MM-DDTHH:MM:SS)")
    
    # DB의 EncryptedString 컬럼 매핑 시 None 및 빈 값 방어
    location: Optional[str] = Field(None, description="장소 (없을 시 None)")
    description: Optional[str] = Field(None, description="일정 상세 내용 및 주의사항")
    
    target_grades: List[int] = Field(
        default_factory=list, 
        description="대상 학년 목록 (예: [1, 2], 전학년 공지인 경우 [1, 2, 3, 4])"
    )
    reason: str = Field(..., description="해당 액션을 결정한 이유 (디버깅 및 로그용)")


class RAGAnalysisResult(BaseModel):
    actions: List[ScheduleAction] = Field(
        ..., 
        description="하나의 메시지에서 추출 및 판단된 C/U/D 일정 작업 목록"
    )