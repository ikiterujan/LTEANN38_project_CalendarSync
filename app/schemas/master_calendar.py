#app/schemas/master_calendar.py
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


class MasterScheduleContext(BaseModel):
    """RAG 수행 시 LLM에게 전달할 기존 마스터 일정 단위"""
    id: str
    title: str
    start_datetime: str
    end_datetime: str
    location: Optional[str] = None
    description: Optional[str] = None
    target_grades: List[int] = []

    # Pydantic v2 표준 attribute/dict 자동 매핑 설정
    model_config = ConfigDict(from_attributes=True)