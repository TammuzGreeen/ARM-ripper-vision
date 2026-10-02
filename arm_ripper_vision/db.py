from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from arm_ripper_vision.models import Base,Batch,BatchState,AuditEvent
class Database:
    def __init__(self,url):
        self.engine=create_engine(url,connect_args={"check_same_thread":False} if url.startswith("sqlite") else {})
        self.session_factory=sessionmaker(self.engine,expire_on_commit=False)
    def current_batch(self,s):
        return s.scalar(select(Batch).where(Batch.state.not_in([BatchState.COMPLETED.value,BatchState.STOPPED.value])).order_by(Batch.id.desc()).limit(1))
    def audit(self,s,t,m,b=None): s.add(AuditEvent(batch_id=b,event_type=t,message=m))
