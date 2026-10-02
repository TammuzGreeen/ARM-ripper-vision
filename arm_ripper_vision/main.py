import uvicorn
from arm_ripper_vision.config import get_settings
def run():
    s=get_settings(); uvicorn.run("arm_ripper_vision.app:app",host=s.host,port=s.port,log_level=s.log_level.lower())
if __name__=="__main__": run()
