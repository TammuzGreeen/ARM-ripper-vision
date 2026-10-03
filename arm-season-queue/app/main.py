import asyncio
import base64
import json
import secrets
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


def create_app(settings=None, start_workers=True):
    settings = settings or Settings()
    if not settings.password:
        raise ValueError('Set a nonempty QUEUE_PASSWORD before starting the service')
    controller = Controller(settings)
    camera = Camera(settings,controller.begin_event,controller.recognition_done,controller.release_event)

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
        if filename not in ('app.js','style.css'):
            raise HTTPException(404)
        return FileResponse(Path(__file__).parent.parent/'static'/filename)

    @app.get('/api/state')
    def state():
        return dict(controller.snapshot(),camera=camera.snapshot())

    @app.post('/api/preflight')
    def preflight():
        report = controller.arm.inspect()
        controller.db.put('preflight',report)
        return report

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
        return {'batch':await asyncio.to_thread(controller.activate,body['master'])}

    @app.post('/api/action')
    async def action(request:Request):
        body = json.loads(await limited(request))
        await asyncio.to_thread(controller.action,body['action'],body.get('job'),body.get('disc'))
        return {'ok':True}

    @app.post('/api/camera/calibrate')
    def calibrate():
        with camera.lock:
            camera.calibrate()
            controller.db.invalidate_pending()
        return {'ok':True}

    @app.post('/api/camera/recapture')
    def recapture():
        with camera.lock:
            camera.recapture()
            controller.db.invalidate_pending()
        return {'ok':True}

    @app.post('/api/camera/mode')
    async def camera_mode(request:Request):
        body = json.loads(await limited(request))
        with camera.lock:
            camera.set_mode(body.get('mode'))
            controller.db.invalidate_pending()
        return {'ok':True}

    @app.post('/api/camera/capture')
    def capture():
        return {'event':camera.capture_manual()}

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
        if filename not in ('0.jpg','1.jpg','2.jpg'):
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

