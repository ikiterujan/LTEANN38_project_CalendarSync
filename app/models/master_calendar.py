#app/models/master_calendar.py
import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, func, Boolean
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.models.types import EncryptedString


class MasterCalendar(Base):
    __tablename__ = "master_calendars"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_channel_id = Column(
        String(100), 
        ForeignKey("channels.channel_id", ondelete="CASCADE"), 
        nullable=False
    )
    raw_message_id = Column(String(200), nullable=False)
    
    # EncryptedString 적용: DB 저장 시 자동 암호화, 조회 시(컬럼 projection 포함) 자동 복호화
    title = Column(EncryptedString(1000), nullable=False)
    location = Column(EncryptedString(1000), nullable=True)
    description = Column(EncryptedString(4000), nullable=True)
    
    start_datetime = Column(DateTime(timezone=True), nullable=False)
    end_datetime = Column(DateTime(timezone=True), nullable=False)
    
    #목표학년
    grade1 = Column(Boolean, default=False)
    grade2 = Column(Boolean, default=False)
    grade3 = Column(Boolean, default=False)
    
    content_hash = Column(String(64), nullable=True)
    created_at = Column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        server_onupdate=func.now()
    )

    channel = relationship("Channel", back_populates="master_schedules")
    sync_logs = relationship(
        "UserSyncLog", 
        back_populates="master_schedule", 
        cascade="all, delete-orphan"
    )


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
    synced_at = Column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        server_onupdate=func.now()
    )

    user = relationship("User", back_populates="sync_logs")
    master_schedule = relationship("MasterCalendar", back_populates="sync_logs")