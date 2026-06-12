import json,threading,time,urllib.parse
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
from datetime import datetime
from .config import Settings
from .db import Database
from .runner import VerificationRunner
from .scheduler import next_run_from_cron
settings=Settings.from_env(); db=Database(settings.db_path); runner=VerificationRunner(settings,db); STATIC=Path(__file__).resolve().parent.parent/'static'; _next=next_run_from_cron(settings.schedule,settings.tz())
def loop():
    global _next
    while True:
        now=datetime.now(settings.tz()).replace(second=0,microsecond=0)
        if now>=_next and not runner.running:
            threading.Thread(target=lambda: runner.run(notify=True),daemon=True).start(); _next=next_run_from_cron(settings.schedule,settings.tz())
        time.sleep(30)
class Handler(SimpleHTTPRequestHandler):
    def j(self,o,code=200):
        b=json.dumps(o,default=str,indent=2).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def body(self):
        n=int(self.headers.get('content-length','0') or 0); return json.loads(self.rfile.read(n).decode()) if n else {}
    def do_GET(self):
        p=urllib.parse.urlparse(self.path).path
        if p=='/api/health': return self.j({'ok':True,'running':runner.running,'version':'1.0.0'})
        if p=='/api/status': return self.j({'running':runner.running,'next_run':_next.isoformat(),'latest':db.latest_payload(),'settings':{'schedule':settings.schedule,'timezone':settings.timezone,'sample_size':settings.sample_size,'clients':settings.clients,'backup_root':str(settings.backup_root),'results_file':str(settings.results_file),'urbackup_url':settings.urbackup_url,'qnap_url':settings.qnap_url}})
        if p=='/api/history': return self.j({'runs':db.recent_runs(100)})
        if p=='/api/results.json':
            if settings.results_file.exists(): return self.j(json.loads(settings.results_file.read_text()))
            return self.j({})
        if p=='/': self.path='/index.html'
        return SimpleHTTPRequestHandler.do_GET(self)
    def do_POST(self):
        p=urllib.parse.urlparse(self.path).path
        if p=='/api/run':
            b=self.body(); clients=b.get('clients')
            if isinstance(clients,str): clients=[clients]
            if runner.running: return self.j({'ok':False,'error':'verification already running'},409)
            threading.Thread(target=lambda: runner.run(clients=clients,notify=True),daemon=True).start(); return self.j({'ok':True,'started':True,'clients':clients or settings.clients})
        if p=='/api/settings': db.save_settings(self.body()); return self.j({'ok':True})
        return self.j({'error':'not found'},404)
    def translate_path(self,path):
        path=urllib.parse.urlparse(path).path.lstrip('/') or 'index.html'; safe='/'.join(x for x in path.split('/') if x and x not in ('.','..')); return str(STATIC/safe)
    def log_message(self,fmt,*args): print('%s - %s'%(self.address_string(),fmt%args),flush=True)
def main():
    settings.data_dir.mkdir(parents=True,exist_ok=True); threading.Thread(target=loop,daemon=True).start(); httpd=ThreadingHTTPServer((settings.host,settings.port),Handler); print(f'backup-verify listening on {settings.host}:{settings.port}',flush=True); httpd.serve_forever()
if __name__=='__main__': main()
