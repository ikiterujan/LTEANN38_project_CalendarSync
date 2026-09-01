from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True, nullable=False) # Teams / Azure AAD Object ID
    grade = Column(Integer, nullable=True)                              # 학년 정보
    conversation_id = Column(String, unique=True, index=True)
    service_url = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class MasterCalendar(Base):
    __tablename__ = "master_calendar"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True, nullable=False)   # 소유자 또는 해당되는 유저 ID
    event_id = Column(String, unique=True, index=True)     # 팀즈/MS Graph 원본 이벤트 ID
    title = Column(String, nullable=False)                # 일정/공지 제목
    content = Column(Text, nullable=True)                 # 상세 내용
    grade = Column(Integer, nullable=True, index=True)    # 필터링용 학년
    start_time = Column(DateTime(timezone=True))          # 일정 시작 시각
    end_time = Column(DateTime(timezone=True))            # 일정 종료 시각
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class CalendarEventLog(Base):
    __tablename__ = "calendar_event_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)                  # 사용자 ID
    change_type = Column(String)                          # manual_refresh, teams_crawl 등
    resource_id = Column(String)                          # 변경된 이벤트/게시물 ID
    last_updated_time = Column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now(),
        index=True
    )