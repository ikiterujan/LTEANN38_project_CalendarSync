from sqlalchemy import Column, String, Integer, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime

from app.core.database import Base


class UserChannelMapping(Base):
    """사용자와 팀즈 채널 간 N:M 매핑 테이블"""
    __tablename__ = "user_channel_mappings"

    user_id = Column(String(100), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    channel_id = Column(String(100), ForeignKey("channels.channel_id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class User(Base):
    """사용자 정보"""
    __tablename__ = "users"

    id = Column(String(100), primary_key=True)  # MS Graph User ID
    email = Column(String(255), nullable=False, unique=True)
    grade = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True)
    last_active_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    channels = relationship("Channel", secondary="user_channel_mappings", back_populates="users")
    sync_logs = relationship("UserSyncLog", back_populates="user", cascade="all, delete-orphan")


class Channel(Base):
    """팀즈 채널 정보"""
    __tablename__ = "channels"

    channel_id = Column(String(100), primary_key=True)  # Teams Channel ID
    team_id = Column(String(100), nullable=False)
    channel_name = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    users = relationship("User", secondary="user_channel_mappings", back_populates="channels")
    master_schedules = relationship("MasterCalendar", back_populates="channel")