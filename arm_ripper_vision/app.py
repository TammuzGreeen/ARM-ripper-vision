from contextlib import asynccontextmanager
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from arm_ripper_vision.config import get_settings
from arm_ripper_vision.db import Database
from arm_ripper_vision.masterlists import MasterlistStore
from arm_ripper_vision.arm import ArmClient
from arm_ripper_vision.controller import BatchController
from arm_ripper_vision.models import Base

s=get_settings(); s.data_dir.mkdir(parents=True,exist_ok=True)
db=Database(s.resolved_database_url); Base.metadata.create_all(db.engine)
ml=MasterlistStore(s.masterlist_dir); arm=ArmClient(s); ctl=BatchController(s,db,ml,arm)
@asynccontextmanager
async def lifespan(app):
    await ctl.startup_reconcile(); ctl.start_poller(); yield; await ctl.stop_poller(); await arm.close()
app=FastAPI(title="ARM Ripper Vision",version="0.1.0",lifespan=lifespan)

@app.get("/",response_class=HTMLResponse)
async def home():
    lists=ml.load_all()
    with db.session_factory() as session: batch=db.current_batch(session)
    options="".join(f'<option value="{k}">{v.series.name}</option>' for k,v in lists.items())
    state=batch.state if batch else "idle"
    current=f"{batch.masterlist_id} / position {batch.current_index+1}" if batch else "none"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="5">
<title>ARM Ripper Vision</title><style>body{{font:16px system-ui;max-width:800px;margin:40px auto;padding:0 20px}}
.card{{border:1px solid #8886;padding:16px;border-radius:8px;margin:16px 0}}input,select,button{{padding:8px;margin:4px}}</style></head>
<body><h1>ARM Ripper Vision</h1><div class="card"><b>Batch:</b> {state}<br><b>Current:</b> {current}</div>
<form method="post" action="/ui/start"><h2>Start controlled batch</h2><select name="masterlist_id">{options}</select>
<label>Season <input type="number" name="season" min="0" value="1"></label>
<label>Disc <input type="number" name="disc" min="1" value="1"></label><button>Start</button></form>
<p>Masterlists are read-only here. Edit YAML in the mounted masterlist dataset.</p></body></html>"""

@app.post("/ui/start")
async def ui_start(masterlist_id:str=Form(...),season:int=Form(...),disc:int=Form(...)):
    await ctl.start_batch(masterlist_id,season,disc); return RedirectResponse("/",303)
@app.get("/api/health")
async def health(): return {"ok":True,"arm":await arm.health()}
@app.get("/api/masterlists")
def masterlists(): return {k:v.model_dump() for k,v in ml.load_all().items()}
@app.post("/api/batches/start")
async def start(masterlist_id:str,season:int,disc:int):
    b=await ctl.start_batch(masterlist_id,season,disc); return {"id":b.id,"state":b.state}
@app.post("/api/batches/{batch_id}/stop")
async def stop(batch_id:int): await ctl.stop_batch(batch_id); return {"ok":True}
