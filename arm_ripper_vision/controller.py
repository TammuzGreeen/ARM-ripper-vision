import asyncio
from sqlalchemy import select
from arm_ripper_vision.models import Batch,BatchState,SeenJob

def jid(x):
    try:return int(x.get("job_id",x.get("id")))
    except:return None
def status(d): return str((d.get("job") or d).get("status","")).lower()
def tracks(d): return d.get("tracks",[]) if isinstance(d.get("tracks",[]),list) else []

class BatchController:
    def __init__(self,settings,database,masterlists,arm):
        self.s=settings; self.db=database; self.m=masterlists; self.arm=arm; self.task=None; self.lock=asyncio.Lock()
    async def startup_reconcile(self):
        with self.db.session_factory() as s:
            b=self.db.current_batch(s)
            if b:
                b.state=BatchState.RESTART_REVIEW.value; b.error_message="Helper restarted; operator input required."; s.commit()
    def start_poller(self): self.task=asyncio.create_task(self.loop())
    async def stop_poller(self):
        if self.task:
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass
    async def loop(self):
        while True:
            try: await self.tick()
            except Exception: pass
            await asyncio.sleep(self.s.arm_poll_interval_seconds)
    async def start_batch(self,mid,season,disc):
        ml=self.m.get(mid); idx=ml.position_index(season,disc)
        if not await self.arm.health(): raise RuntimeError("ARM API is not reachable")
        with self.db.session_factory() as s:
            if self.db.current_batch(s): raise RuntimeError("A batch is already active")
        prev=await self.arm.get_ripping_enabled(); await self.arm.set_ripping_enabled(False); jobs=await self.arm.list_jobs()
        with self.db.session_factory() as s:
            b=Batch(masterlist_id=mid,state=BatchState.WAITING_DISC.value,current_index=idx,previous_ripping_enabled=prev); s.add(b); s.flush()
            for j in jobs:
                if jid(j)!=None:s.add(SeenJob(batch_id=b.id,arm_job_id=jid(j)))
            s.commit(); s.refresh(b); return b
    async def stop_batch(self,bid):
        with self.db.session_factory() as s:b=s.get(Batch,bid); restore=b.previous_ripping_enabled
        if restore is not None: await self.arm.set_ripping_enabled(restore)
        with self.db.session_factory() as s:b=s.get(Batch,bid); b.state=BatchState.STOPPED.value;s.commit()
    async def tick(self):
        async with self.lock:
            with self.db.session_factory() as s:
                b=self.db.current_batch(s)
                if not b:return
                bid,state=b.id,b.state
            if state==BatchState.WAITING_DISC.value: await self.new_job(bid)
            elif state in (BatchState.PREPARING_JOB.value,BatchState.RIPPING.value): await self.poll_job(bid)
    async def new_job(self,bid):
        jobs=await self.arm.list_jobs()
        with self.db.session_factory() as s:
            seen=set(s.scalars(select(SeenJob.arm_job_id).where(SeenJob.batch_id==bid)).all())
            new=sorted(x for x in (jid(j) for j in jobs) if x is not None and x not in seen)
            if not new:return
            j=new[0]; s.add(SeenJob(batch_id=bid,arm_job_id=j)); b=s.get(Batch,bid); b.current_job_id=j;b.state=BatchState.PREPARING_JOB.value;s.commit()
        await self.poll_job(bid)
    async def poll_job(self,bid):
        with self.db.session_factory() as s:b=s.get(Batch,bid); j=b.current_job_id; state=b.state
        d=await self.arm.get_job_detail(j); st=status(d)
        if state==BatchState.PREPARING_JOB.value:
            if st not in ("manual_paused","paused","manual_wait","waiting"): return await self.fail(bid,f"ARM job not paused: {st}")
            if not tracks(d): return
            try: await self.configure(bid,d)
            except Exception as e: await self.fail(bid,str(e))
        elif state==BatchState.RIPPING.value:
            if st=="success": await self.advance(bid)
            elif st in ("fail","failed","error"): await self.fail(bid,f"ARM job failed: {st}")
    async def configure(self,bid,d):
        with self.db.session_factory() as s:
            b=s.get(Batch,bid); ml=self.m.get(b.masterlist_id); pos=ml.flatten_discs()[b.current_index]; j=b.current_job_id
        cfg=d.get("config",{})
        if int(cfg.get("MAXLENGTH",0) or 0)>self.s.max_safe_maxlength: raise RuntimeError("Unsafe MAXLENGTH")
        if cfg.get("ALLOW_DUPLICATES") is False: raise RuntimeError("ALLOW_DUPLICATES must be true")
        if cfg.get("MANUAL_WAIT") is False: raise RuntimeError("MANUAL_WAIT must be true")
        await self.arm.update_job_title(j,{"title":ml.series.name,"year":str(ml.series.year) if ml.series.year else None,"video_type":"series","season":pos.season,"disc_number":pos.disc})
        ts=sorted((t for t in tracks(d) if not t.get("skip_reason") and t.get("process",True) is not False),key=lambda t:int(t["track_number"])); used=set()
        for ep in pos.entry.episodes:
            t=next((x for x in ts if int(x["track_number"])==ep.source_title and int(x["track_number"]) not in used),None) or next(x for x in ts if int(x["track_number"]) not in used)
            used.add(int(t["track_number"])); suffix=f" [{ep.edition}]" if ep.edition else ""
            f={"episode_number":ep.episode,"episode_name":ep.title,"custom_filename":f"{ml.series.name} - S{pos.season:02d}E{ep.episode:02d} - {ep.title}{suffix}"}
            if ep.source_title is not None and t.get("enabled") is False:f["enabled"]=True
            await self.arm.update_track(j,int(t.get("track_id",t.get("id"))),f)
        await self.arm.start_job(j)
        with self.db.session_factory() as s:b=s.get(Batch,bid);b.state=BatchState.RIPPING.value;s.commit()
    async def advance(self,bid):
        with self.db.session_factory() as s:
            b=s.get(Batch,bid); ml=self.m.get(b.masterlist_id); b.current_index+=1;b.current_job_id=None
            if b.current_index>=len(ml.flatten_discs()): restore=b.previous_ripping_enabled;b.state=BatchState.COMPLETED.value
            else: restore=None;b.state=BatchState.WAITING_DISC.value
            s.commit()
        if restore is not None: await self.arm.set_ripping_enabled(restore)
    async def fail(self,bid,msg):
        with self.db.session_factory() as s:b=s.get(Batch,bid);b.state=BatchState.NEEDS_INPUT.value;b.error_message=msg;s.commit()
