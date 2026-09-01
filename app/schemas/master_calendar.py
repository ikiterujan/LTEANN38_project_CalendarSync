from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel


class MasterScheduleContext(BaseModel):
    """RAG 수행 시 LLM에게 전달할 기존 마스터 일정 단위"""
    id: str
    title: str
    start_datetime: str
    end_datetime: str
    location: Optional[str] = None
    description: Optional[str] = None
    target_grades: List[int] = []

    class Config:
        from_attributes = True