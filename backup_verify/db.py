import json, sqlite3, threading
class Database:
    def __init__(self,path):
        self.path=path; self.lock=threading.Lock(); path.parent.mkdir(parents=True,exist_ok=True); self.init()
    def connect(self):
        con=sqlite3.connect(self.path,timeout=30); con.row_factory=sqlite3.Row; return con
    def init(self):
        schema=[
        'CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT "", payload TEXT NOT NULL DEFAULT "{}")',
        'CREATE TABLE IF NOT EXISTS client_results(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, client TEXT NOT NULL, status TEXT NOT NULL, last_checked TEXT NOT NULL, files_checked INTEGER NOT NULL DEFAULT 0, files_failed INTEGER NOT NULL DEFAULT 0, warnings INTEGER NOT NULL DEFAULT 0, details TEXT NOT NULL DEFAULT "{}")',
        'CREATE TABLE IF NOT EXISTS disk_results(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, temperature INTEGER, reallocated INTEGER, pending INTEGER, uncorrectable INTEGER, power_on_hours INTEGER, details TEXT NOT NULL DEFAULT "{}")',
        'CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)']
        with self.connect() as con:
            for s in schema: con.execute(s)
            con.commit()
    def create_run(self, started_at):
        with self.lock,self.connect() as con:
            cur=con.execute('INSERT INTO runs(started_at,status) VALUES (?,?)',(started_at,'running')); con.commit(); return int(cur.lastrowid)
    def finish_run(self, run_id, finished_at, status, summary, payload):
        with self.lock,self.connect() as con:
            con.execute('UPDATE runs SET finished_at=?,status=?,summary=?,payload=? WHERE id=?',(finished_at,status,summary,json.dumps(payload,sort_keys=True),run_id)); con.commit()
    def add_client_result(self, run_id, r):
        with self.lock,self.connect() as con:
            con.execute('INSERT INTO client_results(run_id,client,status,last_checked,files_checked,files_failed,warnings,details) VALUES (?,?,?,?,?,?,?,?)',(run_id,r['client'],r['status'],r['last_checked'],r.get('files_checked',0),r.get('files_failed',0),len(r.get('warnings',[])),json.dumps(r,sort_keys=True))); con.commit()
    def add_disk_result(self, run_id, r):
        with self.lock,self.connect() as con:
            con.execute('INSERT INTO disk_results(run_id,name,status,temperature,reallocated,pending,uncorrectable,power_on_hours,details) VALUES (?,?,?,?,?,?,?,?,?)',(run_id,r.get('name','unknown'),r.get('status','unknown'),r.get('temperature'),r.get('reallocated'),r.get('pending'),r.get('uncorrectable'),r.get('power_on_hours'),json.dumps(r,sort_keys=True))); con.commit()
    def recent_runs(self, limit=25):
        with self.connect() as con: return [dict(r) for r in con.execute('SELECT * FROM runs ORDER BY id DESC LIMIT ?',(limit,))]
    def latest_payload(self):
        with self.connect() as con:
            row=con.execute('SELECT payload FROM runs WHERE status != ? ORDER BY id DESC LIMIT 1',('running',)).fetchone()
            if not row: return {}
            try: return json.loads(row['payload'])
            except Exception: return {}
    def save_settings(self, items):
        with self.lock,self.connect() as con:
            for k,v in items.items(): con.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,str(v)))
            con.commit()
