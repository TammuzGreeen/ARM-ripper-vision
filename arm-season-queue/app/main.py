import asyncio
import base64
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .camera import Camera
from .config import Settings
from .controller import Controller
from .formats import Masterlist
from .handover import beneath
from .vision import VisionError
from .state import encode


def current_workflow_stage(snapshot, camera_state, *, ttl, now=None):
    """Choose a live workflow stage without promoting completed history."""
    now=time.time() if now is None else now
    stage=camera_state.get('preparation_stage')
    active_states={'reserved','starting','ripping','validating','awaiting_review','needs_attention'}
    active=[r for r in snapshot.get('reservations',[])
            if r.get('workflow')=='local-beta-v1' and r.get('state') in active_states]
    if active:
        latest=active[0]
        if latest.get('state') in ('awaiting_review','needs_attention'):
            return 'awaiting_review'
        if latest.get('state') in ('starting','ripping','validating'):
            return 'ripping'
        return 'recognising_scanning'
    if any(e.get('job') is None and e.get('status') in ('processing','ready')
           and e.get('body',{}).get('tray_cycle')
           and now-float(e.get('created') or 0)<ttl
           for e in snapshot.get('events',[])):
        return 'recognising_scanning'
    return stage


def create_app(settings=None, start_workers=True):
    settings = settings or Settings()
    if not settings.password:
        raise ValueError('Set a nonempty QUEUE_PASSWORD before starting the service')
    controller = Controller(settings)
    camera = Camera(settings,controller.begin_event,controller.recognition_done,controller.release_event,
                    controller.masters,controller.close_tray_after_snapshot,
                    controller.annotate_event_tray_cycle)
    controller.on_tray_open=camera.tray_opened
    controller.on_arm_ejected=camera.note_arm_ejected
    controller.get_tray_status=camera.snapshot
    camera.on_event_invalidated=controller.invalidate_camera_event

    @asynccontextmanager
    async def lifespan(app):
        if start_workers:
            controller.start()
            camera.start()
        yield
        if start_workers:
            camera.close()
            controller.close()

    app = FastAPI(title='arm-season-queue',version='0.1.1',lifespan=lifespan)
    app.state.controller = controller
    app.state.camera = camera

    @app.middleware('http')
    async def protection(request, call_next):
        if request.url.path=='/health':
            return JSONResponse({'service':'arm-season-queue','alive':True})
        try:
            kind,token = request.headers.get('authorization','').split(' ',1)
            user,password = base64.b64decode(token,validate=True).decode().split(':',1)
            allowed = (kind.lower()=='basic' and secrets.compare_digest(user.encode(),settings.user.encode())
                       and secrets.compare_digest(password.encode(),settings.password.encode()))
        except (ValueError,UnicodeError):
            allowed = False
        if not allowed:
            return Response(status_code=401,headers={'WWW-Authenticate':'Basic realm="arm-season-queue"'})
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin = request.headers.get('origin')
            if request.headers.get('x-queue-request')!='1' or (origin and urlsplit(origin).netloc!=request.headers.get('host')):
                return JSONResponse({'detail':'Same-origin action header required'},status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        if isinstance(exc, VisionError):
            return JSONResponse({'detail':str(exc),'diagnostics':exc.diagnostic()},status_code=422)
        return JSONResponse({'detail':str(exc)},status_code=400)

    @app.exception_handler(Exception)
    async def unavailable(request, exc):
        # Never expose HTTP request headers or credentials in browser errors.
        return JSONResponse({'detail':'Operation failed. Check connection settings and the queue status.'},status_code=503)

    async def limited(request,limit=2*1024*1024):
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content)>limit:
                raise HTTPException(413,'Request is too large')
        return bytes(content)

    @app.get('/')
    def index():
        return FileResponse(Path(__file__).parent.parent/'static'/'index.html')

    @app.get('/static/{filename}')
    def static(filename:str):
        if filename not in ('app.js','style.css','dry-run.js','beta-review.js','recognition-log.js','context.js'):
            raise HTTPException(404)
        return FileResponse(Path(__file__).parent.parent/'static'/filename)

    @app.get('/api/state')
    def state():
        snapshot=controller.snapshot()
        camera_state=camera.snapshot()
        stage=current_workflow_stage(snapshot,camera_state,ttl=settings.ttl)
        return dict(snapshot,camera=camera_state,workflow_stage=stage)

    @app.get('/api/events')
    def recognition_events(offset:int=0,limit:int=20):
        if offset<0 or not 1<=limit<=100:
            raise HTTPException(400,'offset must be nonnegative and limit must be 1..100')
        rows=controller.db.rows('SELECT * FROM events ORDER BY created DESC')
        rows=[row for row in rows if not str(json.loads(row['body']).get('source','')).startswith('dry_run_')]
        page=rows[offset:offset+limit]
        for row in page:row['body']=json.loads(row['body'])
        return {'items':page,'offset':offset,'limit':limit,'total':len(rows),'has_more':offset+len(page)<len(rows)}

    @app.get('/api/events/{event_id}/evidence')
    def recognition_evidence(event_id:str):
        rows=controller.db.rows('SELECT 1 FROM events WHERE id=?',(event_id,))
        if len(rows)!=1:raise HTTPException(404,'Recognition event not found')
        directory=settings.state/'evidence'/event_id
        items=[]
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                if path.is_file() and path.suffix.lower() in ('.jpg','.jpeg','.png','.webp'):
                    items.append({'name':path.name,'bytes':path.stat().st_size})
        return {'items':items}

    @app.post('/api/events/{event_id}/assessment')
    async def event_assessment(event_id:str,request:Request):
        body=json.loads(await limited(request))
        outcome=body.get('outcome');note=str(body.get('note') or '')[:1000]
        if outcome not in ('correct','incorrect'):
            raise HTTPException(400,'outcome must be correct or incorrect')
        rows=controller.db.rows('SELECT body,status,created FROM events WHERE id=?',(event_id,))
        if len(rows)!=1:
            raise HTTPException(404,'Recognition event not found')
        record=json.loads(rows[0]['body']);history=record.setdefault('diagnostic_assessments',[])
        prior=history[-1] if history else {}
        history.append({'revision':len(history)+1,'outcome':outcome,'note':note,
                        'assessed_result':__import__('hashlib').sha256(json.dumps(record.get('result'),sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                        'supersedes':prior.get('revision'),'assessed_at':__import__('time').time()})
        controller.db.execute('UPDATE events SET body=? WHERE id=?',(encode(record),event_id))
        return {'event':event_id,'assessment':history[-1]}

    @app.get('/api/rejected-rips')
    def rejected_rips(status: str = 'pending'):
        try:
            return {'items': controller.db.rejections(status)}
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.get('/api/rejected-rips/{rejection_id}')
    def rejected_rip(rejection_id: str):
        row = controller.db.rejection(rejection_id)
        if not row:
            raise HTTPException(404)
        return row

    @app.post('/api/rejected-rips/{rejection_id}/metadata')
    async def rejected_metadata(rejection_id: str, request: Request):
        body = json.loads(await limited(request))
        return await asyncio.to_thread(controller.save_rejection_review, rejection_id,
                                       body.get('metadata'), body.get('status', 'corrected'))

    @app.post('/api/rejected-rips/{rejection_id}/resolve')
    async def rejected_resolve(rejection_id: str, request: Request):
        body = json.loads(await limited(request))
        return await asyncio.to_thread(controller.resolve_rejection, rejection_id,
                                       body.get('status', 'resolved'))

    @app.post('/api/preflight')
    def preflight():
        return controller.readiness()

    @app.post('/api/masters')
    async def import_master(request:Request):
        content = await limited(request)
        value = yaml.safe_load(content)
        master = Masterlist.model_validate(value)
        return controller.import_master(master)

    @app.get('/api/schema/masterlist')
    def master_schema():
        return Masterlist.model_json_schema()

    @app.post('/api/batch')
    async def batch(request:Request):
        body = json.loads(await limited(request))
        return {'batch':await asyncio.to_thread(controller.activate,body.get('master'),
                                                 body.get('start_disc'),body.get('start_entry'))}

    @app.post('/api/dry-run')
    async def dry_run(request:Request):
        body = json.loads(await limited(request))
        if camera.mode != 'auto' or not camera.snapshot().get('connected'):
            raise HTTPException(409, 'Dry-run capture requires the connected camera in Automatic mode')
        if camera.event is not None or camera.snapshot().get('busy'):
            raise HTTPException(409, 'Clear the current presentation and wait for camera processing to finish')
        return await asyncio.to_thread(controller.start_dry_run,body.get('master'),body.get('disc'))

    @app.post('/api/dry-run/{run_id}/correction')
    async def dry_run_correction(run_id:str,request:Request):
        body = json.loads(await limited(request))
        return await asyncio.to_thread(controller.save_dry_run_correction,run_id,body)

    @app.post('/api/dry-run/{run_id}/scan')
    async def dry_run_scan(run_id:str,request:Request):
        body = json.loads(await limited(request,2_100_000))
        return await asyncio.to_thread(controller.receive_dry_run_scan,run_id,body.get('info'),body.get('context'))

    @app.post('/api/dry-run/{run_id}/reevaluate-recognition')
    async def dry_run_reevaluate_recognition(run_id:str):
        return await asyncio.to_thread(controller.reevaluate_dry_run_recognition,run_id)

    @app.post('/api/dry-run/{run_id}/assessment')
    async def dry_run_assessment(run_id:str,request:Request):
        body = json.loads(await limited(request))
        return await asyncio.to_thread(controller.assess_dry_run,run_id,body.get('outcome'),body.get('notes',''))

    @app.post('/api/action')
    async def action(request:Request):
        body = json.loads(await limited(request))
        await asyncio.to_thread(controller.action,body['action'],body.get('job'),body.get('disc'))
        return {'ok':True}

    @app.get('/api/local-beta/reviews')
    def local_beta_reviews(status: str = 'all'):
        if not settings.local_beta:
            raise HTTPException(404)
        allowed={'all','awaiting_review','reviewed','accepted','needs_attention','failed'}
        if status not in allowed:
            raise HTTPException(400,'Unknown review filter')
        rows=[r for r in controller.snapshot()['reservations'] if r.get('workflow')=='local-beta-v1']
        if status!='all':
            # ``review`` is the durable recovery state for a completed rip whose
            # post-rip task (for example tray handling) failed. If local review
            # data was already written, that failure must not hide its files
            # from the human-review queue.
            rows=[r for r in rows if r.get('state')==status
                  or (status=='awaiting_review' and r.get('state')=='review'
                      and r.get('local_review') is not None)
                  or (status=='failed' and r.get('state') in ('failed','review'))]
        return {'items':rows}

    @app.post('/api/local-beta/jobs/{job}/review')
    async def local_beta_review(job:int, request:Request):
        body=json.loads(await limited(request))
        return await asyncio.to_thread(controller.review_local_beta,job,body.get('action'),body)

    @app.post('/api/camera/calibrate')
    def calibrate():
        with camera.lock:
            camera.calibrate()
            if not settings.dry_run_only:
                controller.db.invalidate_pending(include_rejections=True)
        return {'ok':True}

    @app.post('/api/camera/recapture')
    def recapture():
        with camera.lock:
            camera.recapture()
            if not settings.dry_run_only:
                controller.db.invalidate_pending(include_rejections=True)
        return {'ok':True}

    @app.post('/api/camera/mode')
    async def camera_mode(request:Request):
        body = json.loads(await limited(request))
        with camera.lock:
            camera.set_mode(body.get('mode'))
            if not settings.dry_run_only:
                controller.db.invalidate_pending(include_rejections=True)
        return {'ok':True}

    @app.post('/api/camera/capture')
    def capture():
        return {'event':camera.capture_manual()}

    @app.post('/api/camera/snapshot')
    def diagnostic_snapshot():
        return {'event':camera.capture_snapshot()}

    def require_diagnostic_event(event):
        rows = controller.db.rows('SELECT body,status,job FROM events WHERE id=?',(event,))
        if not rows:
            raise HTTPException(404)
        body = json.loads(rows[0]['body'])
        # Diagnostic frames remain reviewable after a later capture invalidates
        # their eligibility; they can never be linked to an ARM job.
        if (body.get('source') != 'vision_test'
                or rows[0]['status'] not in ('processing','invalidated','expired','review')
                or rows[0]['job'] is not None):
            raise HTTPException(404)

    @app.post('/api/camera/transcribe/{event}')
    def diagnostic_transcription(event:str):
        require_diagnostic_event(event)
        return camera.transcribe_snapshot(event)

    @app.post('/api/camera/recognize/{event}')
    def diagnostic_recognition(event:str):
        require_diagnostic_event(event)
        return {'event':camera.recognize_snapshot(event),'started':True}

    @app.get('/api/camera/preview')
    async def preview():
        async def frames():
            while True:
                frame = camera.preview
                if frame:
                    yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'+frame+b'\r\n'
                await asyncio.sleep(.15)
        return StreamingResponse(frames(),media_type='multipart/x-mixed-replace; boundary=frame')

    @app.post('/api/photos')
    async def upload(request:Request):
        content = await limited(request,20*1024*1024)
        return {'event':camera.upload(content)}

    @app.get('/api/evidence/{event}/{filename}')
    def evidence(event:str,filename:str):
        if filename not in ('0.jpg','1.jpg','2.jpg','original.jpg','original-0.jpg','original-1.jpg','original-2.jpg','qwen-input.jpg',
                            'agreement-input-0.jpg','agreement-input-1.jpg','agreement-input-2.jpg',
                            'agreement-primary-30b-request.json','agreement-primary-30b-stream.jsonl',
                            'agreement-secondary-7b-request.json','agreement-secondary-7b-stream.jsonl'):
            raise HTTPException(404)
        path = beneath(settings.state/'evidence',event+'/'+filename)
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path)

    @app.post('/api/review')
    async def review(request:Request):
        body = json.loads(await limited(request))
        await asyncio.to_thread(controller.review_event,body['event'],body['master'],body['disc'],body['note'],body.get('job'))
        return {'ok':True}

    return app


def run():
    import uvicorn
    uvicorn.run(create_app(),host='0.0.0.0',port=8080,workers=1)


if __name__=='__main__':
    run()
