import json, threading
from datetime import datetime, timezone
from .checks import verify_client, check_smart, check_qnap
from .b2 import check_b2
from .notify import send_all, format_summary


class VerificationRunner:
    def __init__(self, settings_provider, db):
        self.settings_provider = settings_provider if callable(settings_provider) else (lambda: settings_provider)
        self.db = db
        self.lock = threading.Lock()
        self.running = False
        self.last_error = None

    def current_settings(self):
        return self.settings_provider()

    def run(self, clients=None, notify=True):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('verification already running')
        self.running = True
        try:
            settings = self.current_settings()
            started = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            rid = self.db.create_run(started)
            p = {'run_id': rid, 'started_at': started, 'clients': {}, 'disk_health': {}, 'qnap_health': {}, 'b2': {}}
            for client in (clients or settings.clients):
                r = verify_client(settings, client, self.db)
                p['clients'][client] = {k: v for k, v in r.items() if k != 'client'}
                self.db.add_client_result(rid, r)
            dh = check_smart(settings, self.db)
            p['disk_health'] = dh
            for d in dh.get('drives', []):
                self.db.add_disk_result(rid, d)
            p['qnap_health'] = check_qnap(settings)
            p['b2'] = check_b2(settings)
            statuses = [r.get('status') for r in p['clients'].values()] + [dh.get('status'), p['qnap_health'].get('status')]
            if p['b2'].get('status') not in ('disabled', None):
                statuses.append(p['b2'].get('status'))
            p['overall_status'] = 'failed' if 'failed' in statuses else ('warning' if 'warning' in statuses else 'verified')
            p['finished_at'] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            self.write_results(p, settings)
            summary = format_summary(p)
            self.db.finish_run(rid, p['finished_at'], p['overall_status'], summary, p)
            if notify:
                p['notifications'] = send_all(settings, p, summary, self.db)
            return p
        except Exception as e:
            self.last_error = f'{type(e).__name__}: {e}'
            raise
        finally:
            self.running = False
            self.lock.release()

    def write_results(self, p, settings=None):
        settings = settings or self.current_settings()
        out = {c: {'status': r.get('status'), 'last_checked': r.get('last_checked') or p.get('finished_at'), 'files_checked': r.get('files_checked', 0), 'files_failed': r.get('files_failed', 0)} for c, r in p.get('clients', {}).items()}
        dh = p.get('disk_health', {})
        out['disk_health'] = {'status': dh.get('status'), 'drives_checked': dh.get('drives_checked', 0), 'drives_failed': dh.get('drives_failed', 0)}
        b2 = p.get('b2', {})
        if b2:
            out['b2'] = {'status': b2.get('status'), 'last_run_time': b2.get('last_run_time'), 'offsite_coverage_score': b2.get('offsite_coverage_score')}
        settings.results_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = settings.results_file.with_suffix('.tmp')
        tmp.write_text(json.dumps(out, indent=2, sort_keys=True))
        tmp.replace(settings.results_file)
