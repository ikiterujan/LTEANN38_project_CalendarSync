#app/models/domain.py
# app/models/domain.py
from sqlalchemy import Column, String, Integer, DateTime, Boolean, ForeignKey, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class UserChannelMapping(Base):
    """사용자와 팀즈 채널 간 N:M 매핑 테이블"""
    __tablename__ = "user_channel_mappings"

    user_id = Column(
        String(100), 
        ForeignKey("users.id", ondelete="CASCADE"), 
        primary_key=True
    )
    channel_id = Column(
        String(100), 
        ForeignKey("channels.channel_id", ondelete="CASCADE"), 
        primary_key=True
    )
    created_at = Column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        nullable=False
    )


class User(Base):
    """사용자 정보"""
    __tablename__ = "users"

    id = Column(String(100), primary_key=True)  # MS Graph User ID / Teams User ID
    email = Column(String(255), nullable=True, unique=True)  # Teams 웹훅 대응을 위해 nullable=True 설정
    grade = Column(Integer, nullable=True)
    
    # Teams 1:1 대화 및 웹훅 발송용 필드
    conversation_id = Column(String(255), unique=True, nullable=True)
    service_url = Column(String(255), nullable=True)
    
    # Oracle 호환: BOOLEAN -> NUMBER(1) 자동 대응 및 DB 레벨 Default 설정
    is_active = Column(
        Boolean, 
        default=True, 
        server_default="1", 
        nullable=False
    )
    
    last_active_at = Column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        server_onupdate=func.now()
    )
    created_at = Column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        nullable=False
    )

    channels = relationship(
        "Channel", 
        secondary="user_channel_mappings", 
        back_populates="users"
    )
    sync_logs = relationship(
        "UserSyncLog", 
        back_populates="user", 
        cascade="all, delete-orphan"
    )


class Channel(Base):
    """팀즈 채널 정보"""
    __tablename__ = "channels"

    channel_id = Column(String(100), primary_key=True)  # Teams Channel ID
    team_id = Column(String(100), nullable=False)
    channel_name = Column(String(255), nullable=True)
    
    updated_at = Column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        server_onupdate=func.now()
    )

    users = relationship(
        "User", 
        secondary="user_channel_mappings", 
        back_populates="channels"
    )
    master_schedules = relationship(
        "MasterCalendar", 
        back_populates="channel"
    )