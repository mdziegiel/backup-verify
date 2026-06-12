import json, sqlite3, threading
from datetime import datetime, timezone, timedelta


class Database:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def init(self):
        schema = [
            'CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT "", payload TEXT NOT NULL DEFAULT "{}")',
            'CREATE TABLE IF NOT EXISTS client_results(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, client TEXT NOT NULL, status TEXT NOT NULL, last_checked TEXT NOT NULL, files_checked INTEGER NOT NULL DEFAULT 0, files_failed INTEGER NOT NULL DEFAULT 0, warnings INTEGER NOT NULL DEFAULT 0, details TEXT NOT NULL DEFAULT "{}")',
            'CREATE TABLE IF NOT EXISTS disk_results(id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL, temperature INTEGER, reallocated INTEGER, pending INTEGER, uncorrectable INTEGER, power_on_hours INTEGER, details TEXT NOT NULL DEFAULT "{}")',
            'CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL, details TEXT NOT NULL DEFAULT "{}")',
        ]
        with self.connect() as con:
            for s in schema:
                con.execute(s)
            con.commit()

    def create_run(self, started_at):
        with self.lock, self.connect() as con:
            cur = con.execute('INSERT INTO runs(started_at,status) VALUES (?,?)', (started_at, 'running'))
            con.commit()
            return int(cur.lastrowid)

    def finish_run(self, run_id, finished_at, status, summary, payload):
        with self.lock, self.connect() as con:
            con.execute('UPDATE runs SET finished_at=?,status=?,summary=?,payload=? WHERE id=?', (finished_at, status, summary, json.dumps(payload, sort_keys=True), run_id))
            con.commit()

    def add_client_result(self, run_id, r):
        with self.lock, self.connect() as con:
            con.execute('INSERT INTO client_results(run_id,client,status,last_checked,files_checked,files_failed,warnings,details) VALUES (?,?,?,?,?,?,?,?)', (run_id, r['client'], r['status'], r['last_checked'], r.get('files_checked', 0), r.get('files_failed', 0), len(r.get('warnings', [])), json.dumps(r, sort_keys=True)))
            con.commit()

    def add_disk_result(self, run_id, r):
        with self.lock, self.connect() as con:
            con.execute('INSERT INTO disk_results(run_id,name,status,temperature,reallocated,pending,uncorrectable,power_on_hours,details) VALUES (?,?,?,?,?,?,?,?,?)', (run_id, r.get('name', 'unknown'), r.get('status', 'unknown'), r.get('temperature'), r.get('reallocated'), r.get('pending'), r.get('uncorrectable'), r.get('power_on_hours'), json.dumps(r, sort_keys=True)))
            con.commit()

    def add_notification(self, channel, status, details):
        with self.lock, self.connect() as con:
            con.execute('INSERT INTO notifications(created_at,channel,status,details) VALUES (?,?,?,?)', (datetime.now(timezone.utc).replace(microsecond=0).isoformat(), channel, status, json.dumps(details, sort_keys=True)))
            con.commit()

    def recent_runs(self, limit=25):
        with self.connect() as con:
            return [dict(r) for r in con.execute('SELECT * FROM runs ORDER BY id DESC LIMIT ?', (limit,))]

    def history_details(self, limit=100):
        runs = self.recent_runs(limit)
        for r in runs:
            try:
                r['payload_json'] = json.loads(r.get('payload') or '{}')
            except Exception:
                r['payload_json'] = {}
        return runs

    def latest_payload(self):
        with self.connect() as con:
            row = con.execute('SELECT payload FROM runs WHERE status != ? ORDER BY id DESC LIMIT 1', ('running',)).fetchone()
            if not row:
                return {}
            try:
                return json.loads(row['payload'])
            except Exception:
                return {}

    def save_settings(self, items):
        with self.lock, self.connect() as con:
            for k, v in items.items():
                if isinstance(v, (list, dict)):
                    v = ','.join(str(x) for x in v) if isinstance(v, list) else json.dumps(v)
                con.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (k, str(v)))
            con.commit()

    def load_settings(self):
        with self.connect() as con:
            return {r['key']: r['value'] for r in con.execute('SELECT key,value FROM settings')}

    def latest_client_results(self):
        q = '''SELECT cr.* FROM client_results cr
               JOIN (SELECT client, MAX(id) id FROM client_results GROUP BY client) x ON x.id=cr.id
               ORDER BY cr.client'''
        with self.connect() as con:
            out = []
            for r in con.execute(q):
                d = dict(r)
                try: d['details_json'] = json.loads(d.get('details') or '{}')
                except Exception: d['details_json'] = {}
                out.append(d)
            return out

    def previous_client_details(self, client):
        with self.connect() as con:
            rows = con.execute('SELECT details FROM client_results WHERE client=? ORDER BY id DESC LIMIT 2', (client,)).fetchall()
            if len(rows) < 2:
                return None
            try:
                return json.loads(rows[1]['details'])
            except Exception:
                return None

    def client_summary(self, clients):
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        out = {}
        with self.connect() as con:
            for client in clients:
                last = con.execute('SELECT * FROM client_results WHERE client=? ORDER BY id DESC LIMIT 1', (client,)).fetchone()
                succ = con.execute('SELECT COUNT(*) c FROM client_results WHERE client=? AND status="verified" AND last_checked>=?', (client, since)).fetchone()['c']
                total = con.execute('SELECT COUNT(*) c FROM client_results WHERE client=? AND last_checked>=?', (client, since)).fetchone()['c']
                rows = con.execute('SELECT last_checked,details,status FROM client_results WHERE client=? ORDER BY id DESC LIMIT 30', (client,)).fetchall()
                trend = []
                for r in reversed(rows):
                    try: det = json.loads(r['details'] or '{}')
                    except Exception: det = {}
                    trend.append({'time': r['last_checked'], 'size_bytes': det.get('backup_size_bytes', 0), 'status': r['status']})
                last_details = {}
                if last:
                    try: last_details = json.loads(last['details'] or '{}')
                    except Exception: last_details = {}
                out[client] = {
                    'last_successful_backup': last_details.get('latest_backup_time') or (last['last_checked'] if last and last['status'] == 'verified' else None),
                    'last_run_time': last['last_checked'] if last else None,
                    'last_status': last['status'] if last else 'unknown',
                    'success_rate_30d': round((succ / total) * 100, 1) if total else None,
                    'trend': trend,
                }
        return out

    def disk_trends(self):
        with self.connect() as con:
            rows = con.execute('SELECT name,status,temperature,reallocated,pending,uncorrectable,power_on_hours,details FROM disk_results ORDER BY id DESC LIMIT 200').fetchall()
            by = {}
            for r in rows:
                by.setdefault(r['name'], []).append(dict(r))
            return by
