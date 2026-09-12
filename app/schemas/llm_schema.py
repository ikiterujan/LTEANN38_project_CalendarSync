from typing import List, Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator


class ScheduleAction(BaseModel):
    action: Literal["CREATE", "UPDATE", "DELETE", "SKIP"] = Field(
        ..., 
        description="CREATE: 새 일정 추가, UPDATE: 기존 일정 수정, DELETE: 일정 취소, SKIP: 변경없음 또는 중복"
    )
    master_schedule_id: Optional[str] = Field(
        default=None, 
        description="UPDATE 또는 DELETE일 경우 대상 MasterCalendar ID (CREATE/SKIP일 경우 None)"
    )
    
    title: str = Field(..., description="일정 제목")
    start_datetime: Optional[str] = Field(
        default=None, 
        description="시작 일시 (ISO 8601 형식: YYYY-MM-DDTHH:MM:SS)"
    )
    end_datetime: Optional[str] = Field(
        default=None, 
        description="종료 일시 (ISO 8601 형식: YYYY-MM-DDTHH:MM:SS)"
    )
    
    @field_validator('start_datetime', 'end_datetime', mode='before')
    @classmethod
    def empty_string_to_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator('target_grades')
    @classmethod
    def validate_grades(cls, v: List[int]) -> List[int]:
        # 학년 범위 유효성 검증 (1~3학년)
        valid_grades = [g for g in v if 1 <= g <= 3]
        return valid_grades

    @model_validator(mode='after')
    def validate_action_requirements(self):
        # CREATE나 UPDATE 작업인데 필수 필드인 start_datetime이 없으면
        # 프로세스를 에러로 멈추지 않고 action을 'NONE'으로 안전하게 전환합니다.
        if self.action in ["CREATE", "UPDATE"] and not self.start_datetime:
            self.action = "NONE"
            self.reason = f"[{self.action} 스킵] start_datetime이 누락되어 일정을 처리할 수 없습니다."
        return self

    location: Optional[str] = Field(default=None, description="장소 (없을 시 None)")
    description: Optional[str] = Field(default=None, description="일정 상세 내용 및 주의사항")
    
    target_grades: List[int] = Field(
        default_factory=list, 
        description="대상 학년 목록 (예: [1, 2], 전학년 공지인 경우 [1, 2, 3])"
    )
    reason: str = Field(..., description="해당 액션을 결정한 이유 (디버깅 및 로그용)")


class RAGAnalysisResult(BaseModel):
    actions: List[ScheduleAction] = Field(
        ..., 
        description="하나의 메시지에서 추출 및 판단된 C/U/D 일정 작업 목록"
    )