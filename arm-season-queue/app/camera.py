import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

from .recognition import consensus, ocr
from .vision import identify, VisionError


class PresentationGate:
    """A presentation latches until the calibrated empty scene returns."""
    def __init__(self):
        self.latched = False
        self.stable = 0
        self.empty = 0

    def observe(self, present, stable, sharp):
        if not present:
            self.empty += 1
            self.stable = 0
            if self.empty >= 8 and self.latched:
                self.latched = False
                return 'release'
            return None
        self.empty = 0
        if self.latched:
            return None
        self.stable = self.stable+1 if stable and sharp else 0
        if self.stable >= 8:
            self.latched = True
            self.stable = 0
            return 'capture'
        return None


class Camera:
    def __init__(self, settings, on_begin, on_result, on_release):
        self.s = settings
        self.on_begin, self.on_result, self.on_release = on_begin, on_result, on_release
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='local-ocr')
        self.future = None
        self.preview = None
        self.frame = None
        self.frame_at = 0
        self.mode = settings.camera_mode or ('auto' if settings.arm_url else 'manual')
        self.background = None
        self.previous = None
        self.gate = PresentationGate()
        self.event = None
        self.samples = []
        self.status = {'connected':False, 'message':'Connecting to camera', 'calibrated':False, 'mode':self.mode}

    def snapshot(self):
        with self.lock:
            return dict(self.status, busy=bool(self.future and not self.future.done()), backend=self.s.recognition_backend)

    def begin(self, source):
        return self.on_begin('vision_test' if self.s.recognition_backend != 'tesseract' else source)

    def release(self, event):
        if self.s.recognition_backend == 'tesseract':
            self.on_release(event)

    def require_frame(self):
        if self.frame is None or not self.status['connected'] or time.monotonic()-self.frame_at > 2:
            raise ValueError('No fresh camera frame available; check the live preview')

    def set_mode(self, mode):
        if mode not in ('manual', 'auto'):
            raise ValueError('Choose manual or auto camera mode')
        with self.lock:
            if self.future and not self.future.done():
                raise ValueError('Wait for recognition to finish before changing mode')
            self.mode = mode
            self.event = None
            self.samples = []
            self.background = None
            self.previous = None
            self.gate = PresentationGate()
            self.status.update(mode=mode, calibrated=False, message='Clear the view, then mark it empty')

    def capture_manual(self):
        with self.lock:
            if self.mode != 'manual':
                raise ValueError('Select Manual test mode first')
            self.require_frame()
            if self.background is None:
                raise ValueError('Clear the view and click View is empty first')
            if self.future and not self.future.done():
                raise ValueError('Recognition is busy; wait before capturing again')
            # Freeze the current frame at the click, never a later empty scene.
            image = self.frame.copy()
            event = self.begin('manual_test')
            self.status['message'] = 'Reading your snapshot; you may move the disc now'
            # Test snapshots are never released for association with an ARM insertion.
            self.future = self.pool.submit(self.process, event, [image], True)
            return event

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
        self.pool.shutdown(wait=False, cancel_futures=True)

    def calibrate(self):
        with self.lock:
            self.require_frame()
            if self.future and not self.future.done():
                raise ValueError('Wait for recognition to finish before marking the view empty')
            # Explicit empty confirmation recovers even if automatic detection is latched.
            self.event = None
            self.samples = []
            self.previous = None
            self.background = self.small(self.frame)
            self.gate = PresentationGate()
            self.status['calibrated'] = True
            self.status['message'] = ('Empty view saved. Place the disc, then click Capture disc now'
                                      if self.mode == 'manual' else 'Ready: present media inside the preview')

    @staticmethod
    def small(frame):
        return cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160,100)).astype(np.float32)

    def recapture(self):
        with self.lock:
            if self.mode == 'manual':
                raise ValueError('Use Capture disc now in Manual test mode')
            if self.event or (self.future and not self.future.done()):
                raise ValueError('Remove media and wait for current recognition first')
            self.gate = PresentationGate()

    def run(self):
        cap = None
        while not self.stop.is_set():
            try:
                if cap is None:
                    cap = cv2.VideoCapture(self.s.camera, cv2.CAP_V4L2)
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.s.width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.s.height)
                    cap.set(cv2.CAP_PROP_FPS, self.s.camera_fps)
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError('Camera unavailable: check device mapping and video group')
                x1,y1,x2,y2 = map(float, self.s.roi.split(','))
                if not (0<=x1<x2<=1 and 0<=y1<y2<=1):
                    raise ValueError('CAMERA_ROI must be x1,y1,x2,y2 fractions within 0..1')
                h,w = frame.shape[:2]
                frame = frame[int(h*y1):int(h*y2), int(w*x1):int(w*x2)]
                with self.lock:
                    self.frame = frame
                    self.frame_at = time.monotonic()
                    self.status.update(connected=True, resolution=f'{w}×{h}')
                    preview_height = max(1,round(960*frame.shape[0]/frame.shape[1]))
                    self.preview = cv2.imencode('.jpg', cv2.resize(frame,(960,preview_height)), [cv2.IMWRITE_JPEG_QUALITY,75])[1].tobytes()
                    small = self.small(frame)
                    sharp = cv2.Laplacian(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),cv2.CV_64F).var()
                    glare = float(np.mean(small>250))
                    self.status.update(sharpness=round(sharp), glare=round(glare,2))
                    if self.background is None:
                        self.status['message'] = 'Clear the view, then calibrate the empty background'
                    elif self.mode == 'auto':
                        present = np.mean(np.abs(small-self.background)>25) > .12
                        stable = self.previous is not None and np.mean(np.abs(small-self.previous)) < 4
                        if present and self.event is None:
                            # Invalidate previous evidence on object arrival, even if blurry/unreadable.
                            self.event = self.begin('camera')
                            self.samples = []
                            self.collecting = False
                            self.submitted = False
                        action = self.gate.observe(present, stable, sharp>=self.s.sharpness and glare<.55)
                        # Release also needs to work for an object that never became sharp enough.
                        if not present and self.gate.empty>=8 and self.event:
                            self.release(self.event)
                            if not getattr(self,'submitted',False):
                                self.on_result(self.event, {'accepted':False,'frames':[], 'reason':'Media removed before enough sharp frames were captured'})
                                self.samples = []
                            self.event = None
                        if action == 'capture':
                            if self.future is not None and not self.future.done():
                                self.status['message'] = 'OCR busy; remove media and present again after completion'
                                self.on_result(self.event, {'accepted':False,'frames':[], 'reason':'OCR busy; remove and present this media again'})
                            else:
                                self.samples = []
                                self.collecting = True
                        if self.event and getattr(self,'collecting',False) and present and stable and sharp>=self.s.sharpness:
                            self.samples.append(frame.copy())
                            if len(self.samples) >= 3:
                                images = self.samples
                                self.samples = []
                                self.collecting = False
                                self.submitted = True
                                self.future = self.pool.submit(self.process, self.event, images)
                                self.status['message'] = 'Reading 3 captured frames; remove media before insertion'
                    self.previous = small
                self.stop.wait(.15)
            except Exception as exc:
                with self.lock:
                    self.status.update(connected=False, message=str(exc))
                    self.background = None
                    self.status['calibrated'] = False
                    if self.event:
                        self.on_result(self.event, {'accepted':False, 'frames':[], 'reason':'Camera disconnected; recapture required'})
                    self.event = None
                    self.samples = []
                    self.gate = PresentationGate()
                if cap:
                    cap.release()
                cap = None
                self.stop.wait(3)
        if cap:
            cap.release()

    def process(self, event, images, test_only=False, source='webcam'):
        results = []
        vision = self.s.recognition_backend != 'tesseract'
        try:
            evidence = self.s.state/'evidence'/event
            evidence.mkdir(parents=True, exist_ok=True)
            for i, frame in enumerate(images):
                path = evidence/f'{i}.jpg'
                if not cv2.imwrite(str(path), frame):
                    raise RuntimeError('Could not retain camera evidence')
                # Retain evidence before recognition, including on server failure.
                results.append({'evidence':f'{event}/{i}.jpg'})
            if vision:
                observation = identify(self.s, images, source=source)
                result = {'accepted':False, 'test_only':True, 'frames':results,
                          'backend':self.s.recognition_backend, 'model':self.s.vision_model,
                          'observation':observation,
                          'reason':'Vision observations require review; this first iteration does not authorize ripping.'}
            else:
                for item, frame in zip(results, images):
                    item.update(ocr(frame, self.s.ocr_lang))
                result = consensus(results)
            if test_only and not vision:
                result.update(accepted=False, test_only=True,
                              reason='Manual test snapshot: inspect the image and text below. Not used for ripping.')
            self.on_result(event, result)
            with self.lock:
                self.status['message'] = result['reason']
        except Exception as exc:
            reason = str(exc) if isinstance(exc, VisionError) else 'Image recognition failed; check service configuration and try again'
            self.on_result(event, {'accepted':False, 'test_only':test_only or vision,
                                  'frames':results, 'reason':reason, 'backend':self.s.recognition_backend})
            with self.lock:
                self.status['message'] = reason

    def upload(self, content):
        if self.future and not self.future.done():
            raise ValueError('Recognition is busy')
        # Check compressed-image dimensions before asking OpenCV to allocate a full frame.
        from PIL import Image
        from io import BytesIO
        with Image.open(BytesIO(content)) as im:
            if im.width*im.height > 25000000:
                raise ValueError('Image exceeds 25 megapixels')
            im.verify()
        frame = cv2.imdecode(np.frombuffer(content,dtype=np.uint8),cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError('Unsupported image')
        event = self.begin('upload')
        self.release(event)
        # One uploaded image cannot masquerade as independent camera consensus.
        self.future = self.pool.submit(self.process,event,[frame],False,'photo')
        return event

