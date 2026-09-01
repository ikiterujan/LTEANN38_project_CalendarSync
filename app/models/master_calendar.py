import uuid
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, JSON, func
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.security import encrypt_text, decrypt_text


class MasterCalendar(Base):
    __tablename__ = "master_calendars"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_channel_id = Column(
        String(100), 
        ForeignKey("channels.channel_id", ondelete="CASCADE"), 
        nullable=False
    )
    raw_message_id = Column(String(200), nullable=False)
    
    # [Oracle 최적화] 암호화 필드: CLOB 제한을 피하기 위해 String(2000~4000)으로 매핑
    _title = Column("title", String(1000), nullable=False)
    _location = Column("location", String(1000), nullable=True)
    _description = Column("description", String(4000), nullable=True)  # CLOB(Text) 대신 VARCHAR2(4000) 사용
    
    start_datetime = Column(DateTime(timezone=True), nullable=False)
    end_datetime = Column(DateTime(timezone=True), nullable=False)
    
    # [Oracle 최적화] SQLAlchemy의 JSON 매핑 (Oracle 21c+ / 19c CLOB-JSON 호환)
    target_grades = Column(JSON, default=list)
    
    content_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # --- @property 프로퍼티를 통한 자동 암/복호화 ---
    @property
    def title(self) -> str:
        return decrypt_text(self._title) if self._title else ""

    @title.setter
    def title(self, value: str):
        self._title = encrypt_text(value) if value else ""

    @property
    def location(self) -> str:
        return decrypt_text(self._location) if self._location else None

    @location.setter
    def location(self, value: str):
        self._location = encrypt_text(value) if value else None

    @property
    def description(self) -> str:
        return decrypt_text(self._description) if self._description else None

    @description.setter
    def description(self, value: str):
        self._description = encrypt_text(value) if value else None

    channel = relationship("Channel", back_populates="master_schedules")
    sync_logs = relationship("UserSyncLog", back_populates="master_schedule", cascade="all, delete-orphan")


class UserSyncLog(Base):
    __tablename__ = "user_sync_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(
        String(100), 
        ForeignKey("users.id", ondelete="CASCADE"), 
        nullable=False
    )
    master_schedule_id = Column(
        String(36), 
        ForeignKey("master_calendars.id", ondelete="CASCADE"), 
        nullable=False
    )
    outlook_event_id = Column(String(255), nullable=True)
    synced_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="sync_logs")
    master_schedule = relationship("MasterCalendar", back_populates="sync_logs")