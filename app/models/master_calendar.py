import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.security import encrypt_text, decrypt_text

class MasterCalendar(Base):
    __tablename__ = "master_calendars"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_channel_id = Column(String(100), ForeignKey("channels.channel_id", ondelete="CASCADE"), nullable=False)
    raw_message_id = Column(String(200), nullable=False)
    
    # 실제 DB 컬럼 (암호화된 문자열 저장)
    _title = Column("title", String(500), nullable=False)
    _location = Column("location", String(500), nullable=True)
    _description = Column("description", Text, nullable=True)
    
    start_datetime = Column(DateTime, nullable=False)
    end_datetime = Column(DateTime, nullable=False)
    target_grades = Column(JSON, default=list)
    content_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # --- @property 프로퍼티를 통한 자동 암/복호화 ---
    @property
    def title(self) -> str:
        return decrypt_text(self._title)

    @title.setter
    def title(self, value: str):
        self._title = encrypt_text(value)

    @property
    def location(self) -> str:
        return decrypt_text(self._location)

    @location.setter
    def location(self, value: str):
        self._location = encrypt_text(value)

    @property
    def description(self) -> str:
        return decrypt_text(self._description)

    @description.setter
    def description(self, value: str):
        self._description = encrypt_text(value)

    channel = relationship("Channel", back_populates="master_schedules")
    sync_logs = relationship("UserSyncLog", back_populates="master_schedule", cascade="all, delete-orphan")


class UserSyncLog(Base):
    __tablename__ = "user_sync_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(100), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    master_schedule_id = Column(String(36), ForeignKey("master_calendars.id", ondelete="CASCADE"), nullable=False)
    outlook_event_id = Column(String(255), nullable=True)
    synced_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="sync_logs")
    master_schedule = relationship("MasterCalendar", back_populates="sync_logs")