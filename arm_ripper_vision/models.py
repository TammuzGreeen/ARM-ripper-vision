from datetime import datetime,timezone
from enum import StrEnum
from sqlalchemy import Boolean,DateTime,Integer,String,Text
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column
def now(): return datetime.now(timezone.utc)
class Base(DeclarativeBase): pass
class BatchState(StrEnum):
    WAITING_DISC="waiting_disc"; PREPARING_JOB="preparing_job"; RIPPING="ripping"; NEEDS_INPUT="needs_input"; RESTART_REVIEW="restart_review"; COMPLETED="completed"; STOPPED="stopped"
class Batch(Base):
    __tablename__="batches"
    id:Mapped[int]=mapped_column(primary_key=True)
    masterlist_id:Mapped[str]=mapped_column(String(200))
    state:Mapped[str]=mapped_column(String(50))
    current_index:Mapped[int]=mapped_column(Integer,default=0)
    previous_ripping_enabled:Mapped[bool|None]=mapped_column(Boolean,nullable=True)
    current_job_id:Mapped[int|None]=mapped_column(Integer,nullable=True)
    error_message:Mapped[str|None]=mapped_column(Text,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
    updated_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now)
class SeenJob(Base):
    __tablename__="seen_jobs"
    id:Mapped[int]=mapped_column(primary_key=True)
    batch_id:Mapped[int]=mapped_column(index=True)
    arm_job_id:Mapped[int]=mapped_column()
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class AuditEvent(Base):
    __tablename__="audit_events"
    id:Mapped[int]=mapped_column(primary_key=True)
    batch_id:Mapped[int|None]=mapped_column(nullable=True,index=True)
    event_type:Mapped[str]=mapped_column(String(100))
    message:Mapped[str]=mapped_column(Text)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
