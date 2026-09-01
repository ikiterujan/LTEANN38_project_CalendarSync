from sqlalchemy import Column, String, DateTime, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class MasterCalendar(Base):
    """마스터 캘린더 (Single Source of Truth)"""
    __tablename__ = "master_calendars"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_channel_id = Column(String(100), ForeignKey("channels.channel_id", ondelete="CASCADE"), nullable=False)
    raw_message_id = Column(String(200), nullable=False)
    
    title = Column(String(255), nullable=False)
    start_datetime = Column(DateTime, nullable=False)
    end_datetime = Column(DateTime, nullable=False)
    location = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    target_grades = Column(JSON, default=list)
    
    content_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    channel = relationship("Channel", back_populates="master_schedules")
    sync_logs = relationship("UserSyncLog", back_populates="master_schedule", cascade="all, delete-orphan")


class UserSyncLog(Base):
    """Fan-out 동기화 추적 테이블 (Master Event <-> User Outlook Event ID 매핑)"""
    __tablename__ = "user_sync_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(100), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    master_schedule_id = Column(String(36), ForeignKey("master_calendars.id", ondelete="CASCADE"), nullable=False)
    
    outlook_event_id = Column(String(255), nullable=True)  # Graph API가 반환한 유저 개인 event_id
    synced_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="sync_logs")
    master_schedule = relationship("MasterCalendar", back_populates="sync_logs")