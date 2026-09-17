"""Bounded budgets shared by Gateway and personal executors, by route behaviour."""
from dataclasses import dataclass
import httpx
from maintenance_routes import classify

@dataclass(frozen=True)
class Budget:
    connect:float
    read:float             # first response and idle between received chunks
    total:float            # includes the complete stream, not reset by chunks
    cleanup:float=5
    gateway_margin:float=30
    def httpx(self,*,gateway=False):
        return httpx.Timeout(connect=3 if gateway else self.connect,read=self.read+(30 if gateway else 0),write=self.read,pool=self.connect)
    @property
    def gateway_total(self):return self.total+self.cleanup+self.gateway_margin

TEXT=Budget(10,300,1800)
GENERATION=Budget(20,300,1800)
PROBE=Budget(10,30,60)
NORMAL=Budget(10,120,180)

def for_route(method,path):
    kind=classify(method,path)
    if kind in {'llm','llm_stream','agent','caption','classification'}:return TEXT
    if kind in {'image','video'}:return GENERATION
    if kind=='provider_probe':return PROBE
    return NORMAL

def for_execution(purpose,total_limit=None):
    base=TEXT if purpose=='llm' else GENERATION
    total=min(base.total,float(total_limit)) if total_limit else base.total
    return Budget(min(base.connect,total),min(base.read,total),total,base.cleanup)
