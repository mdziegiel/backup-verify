import json,threading
from datetime import datetime,timezone
from .checks import verify_client,check_smart,check_qnap
from .notify import send_telegram,format_summary
class VerificationRunner:
    def __init__(self,settings,db): self.settings=settings; self.db=db; self.lock=threading.Lock(); self.running=False; self.last_error=None
    def run(self,clients=None,notify=True):
        if not self.lock.acquire(blocking=False): raise RuntimeError('verification already running')
        self.running=True
        try:
            started=datetime.now(timezone.utc).replace(microsecond=0).isoformat(); rid=self.db.create_run(started); p={'run_id':rid,'started_at':started,'clients':{},'disk_health':{},'qnap_health':{}}
            for client in (clients or self.settings.clients):
                r=verify_client(self.settings,client); p['clients'][client]={k:v for k,v in r.items() if k!='client'}; self.db.add_client_result(rid,r)
            dh=check_smart(self.settings); p['disk_health']=dh
            for d in dh.get('drives',[]): self.db.add_disk_result(rid,d)
            p['qnap_health']=check_qnap(self.settings)
            statuses=[r.get('status') for r in p['clients'].values()]+[dh.get('status'),p['qnap_health'].get('status')]
            p['overall_status']='failed' if 'failed' in statuses else ('warning' if 'warning' in statuses else 'verified')
            p['finished_at']=datetime.now(timezone.utc).replace(microsecond=0).isoformat(); self.write_results(p); summary=format_summary(p); self.db.finish_run(rid,p['finished_at'],p['overall_status'],summary,p)
            if notify and (p['overall_status']!='verified' or self.settings.alert_on_success):
                try: p['telegram']=send_telegram(self.settings,summary)
                except Exception as e: p['telegram']={'sent':False,'error':f'{type(e).__name__}: {e}'}
            return p
        except Exception as e: self.last_error=f'{type(e).__name__}: {e}'; raise
        finally: self.running=False; self.lock.release()
    def write_results(self,p):
        out={c:{'status':r.get('status'),'last_checked':r.get('last_checked') or p.get('finished_at'),'files_checked':r.get('files_checked',0),'files_failed':r.get('files_failed',0)} for c,r in p.get('clients',{}).items()}
        dh=p.get('disk_health',{}); out['disk_health']={'status':dh.get('status'),'drives_checked':dh.get('drives_checked',0),'drives_failed':dh.get('drives_failed',0)}
        self.settings.results_file.parent.mkdir(parents=True,exist_ok=True); tmp=self.settings.results_file.with_suffix('.tmp'); tmp.write_text(json.dumps(out,indent=2,sort_keys=True)); tmp.replace(self.settings.results_file)
