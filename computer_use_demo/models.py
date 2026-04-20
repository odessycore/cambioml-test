import json
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.orm import selectinload

from .database import Base

class AgentSession(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, index=True)
    display_num = Column(Integer, nullable=False)
    novnc_port = Column(Integer, nullable=False)
    status = Column(String, default="running")
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "display_num": self.display_num,
            "novnc_port": self.novnc_port,
            "status": self.status,
            "created_at": self.created_at.isoformat()
        }

class AgentMessage(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(String, index=True)
    role = Column(String, nullable=False) # 'user', 'assistant', 'system'
    content = Column(Text, nullable=False) # JSON encoded block
    created_at = Column(DateTime, default=datetime.utcnow)

    def get_content(self):
        try:
            return json.loads(self.content)
        except json.JSONDecodeError:
            return self.content
