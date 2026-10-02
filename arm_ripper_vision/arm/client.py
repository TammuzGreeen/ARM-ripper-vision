from typing import Any
import httpx
from arm_ripper_vision.config import Settings
class ArmClientError(RuntimeError): pass
class ArmClient:
    def __init__(self,s:Settings):
        self.s=s; headers={"Accept":"application/json"}; auth=None
        if s.arm_auth_type=="bearer" and s.arm_auth_token: headers["Authorization"]=f"Bearer {s.arm_auth_token}"
        elif s.arm_auth_type=="header" and s.arm_auth_token: headers[s.arm_auth_header]=s.arm_auth_token
        elif s.arm_auth_type=="basic": auth=httpx.BasicAuth(s.arm_auth_username or "",s.arm_auth_password or "")
        self.c=httpx.AsyncClient(base_url=s.arm_base_url.rstrip("/"),headers=headers,auth=auth,timeout=s.arm_timeout_seconds)
    async def close(self): await self.c.aclose()
    async def req(self,m,p,**kw):
        try:
            r=await self.c.request(m,p,**kw); r.raise_for_status(); return r.json() if r.content else None
        except (httpx.HTTPError,ValueError) as e: raise ArmClientError(f"{m} {p}: {e}") from e
    async def health(self):
        try: await self.get_ripping_enabled(); return True
        except ArmClientError: return False
    async def list_jobs(self):
        x=await self.req("GET",self.s.arm_jobs_path)
        if isinstance(x,list): return x
        for k in ("jobs","results","items"):
            if isinstance(x,dict) and isinstance(x.get(k),list): return x[k]
        raise ArmClientError("Unsupported jobs payload")
    async def get_job_detail(self,j): return await self.req("GET",self.s.arm_job_detail_path.format(job_id=j))
    async def update_job_title(self,j,f): return await self.req("PUT",self.s.arm_job_title_path.format(job_id=j),json=f)
    async def update_track(self,j,t,f): return await self.req("PUT",self.s.arm_track_update_path.format(job_id=j,track_id=t),json=f)
    async def start_job(self,j): return await self.req("POST",self.s.arm_job_start_path.format(job_id=j))
    async def get_ripping_enabled(self):
        x=await self.req("GET",self.s.arm_ripping_enabled_path)
        if isinstance(x,bool): return x
        for k in ("enabled","ripping_enabled","ripping-enabled"):
            if isinstance(x,dict) and k in x: return bool(x[k])
        raise ArmClientError("Unsupported ripping-enabled payload")
    async def set_ripping_enabled(self,v):
        try: return await self.req("POST",self.s.arm_ripping_enabled_path,json={"enabled":v})
        except ArmClientError: return await self.req("POST",self.s.arm_ripping_enabled_path,json={"ripping_enabled":v})
