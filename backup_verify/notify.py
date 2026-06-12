import json, smtplib, urllib.parse, urllib.request
from datetime import datetime
from email.message import EmailMessage


def in_quiet_hours(settings, now=None):
    if not settings.quiet_hours_start or not settings.quiet_hours_end:
        return False
    now = now or datetime.now(settings.tz())
    cur = now.strftime('%H:%M')
    start, end = settings.quiet_hours_start, settings.quiet_hours_end
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end


def should_notify(settings, payload):
    status = payload.get('overall_status')
    if in_quiet_hours(settings):
        return False, 'quiet hours active'
    if settings.notify_on_failure_only:
        return status == 'failed', 'failure-only trigger'
    if status == 'failed':
        return True, 'failure trigger'
    if status == 'warning' and settings.notify_on_warning:
        return True, 'warning trigger'
    if status == 'verified' and settings.notify_on_completion:
        return True, 'completion trigger'
    return False, 'no trigger matched'


def send_telegram(settings, text):
    if not settings.telegram_enabled:
        return {'sent': False, 'reason': 'telegram disabled'}
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return {'sent': False, 'reason': 'telegram not configured'}
    data = urllib.parse.urlencode({'chat_id': settings.telegram_chat_id, 'text': text[:3900], 'disable_web_page_preview': 'true'}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage', data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read().decode()
    try:
        return json.loads(body)
    except Exception:
        return {'sent': True, 'raw': body[:200]}


def send_gotify(settings, title, text):
    if not settings.gotify_enabled:
        return {'sent': False, 'reason': 'gotify disabled'}
    if not settings.gotify_url or not settings.gotify_token:
        return {'sent': False, 'reason': 'gotify not configured'}
    data = urllib.parse.urlencode({'title': title, 'message': text[:3900], 'priority': '6'}).encode()
    url = settings.gotify_url.rstrip('/') + '/message?token=' + urllib.parse.quote(settings.gotify_token)
    with urllib.request.urlopen(urllib.request.Request(url, data=data, method='POST'), timeout=15) as r:
        body = r.read().decode()
    try:
        return json.loads(body)
    except Exception:
        return {'sent': True, 'raw': body[:200]}


def send_email(settings, subject, text):
    if not settings.smtp_enabled:
        return {'sent': False, 'reason': 'smtp disabled'}
    if not settings.smtp_server or not settings.smtp_to:
        return {'sent': False, 'reason': 'smtp server/to not configured'}
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = settings.smtp_from
    msg['To'] = settings.smtp_to
    msg.set_content(text)
    with smtplib.SMTP(settings.smtp_server, settings.smtp_port, timeout=20) as smtp:
        smtp.ehlo()
        if settings.smtp_port in (587, 25):
            try:
                smtp.starttls(); smtp.ehlo()
            except Exception:
                pass
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(msg)
    return {'sent': True, 'to': settings.smtp_to}


def send_all(settings, payload, text, db=None, force=False):
    ok, reason = (True, 'forced') if force else should_notify(settings, payload)
    if not ok:
        return {'sent': False, 'reason': reason, 'channels': {}}
    results = {}
    for channel, fn, args in [
        ('telegram', send_telegram, (settings, text)),
        ('gotify', send_gotify, (settings, 'Backup Verify', text)),
        ('email', send_email, (settings, 'Backup Verify summary', text)),
    ]:
        try:
            results[channel] = fn(*args)
        except Exception as e:
            results[channel] = {'sent': False, 'error': f'{type(e).__name__}: {e}'}
        if db:
            db.add_notification(channel, 'sent' if results[channel].get('sent') or results[channel].get('ok') else 'skipped', results[channel])
    return {'sent': any(v.get('sent') or v.get('ok') for v in results.values()), 'reason': reason, 'channels': results}


def format_summary(p):
    lines = ['Backup Verify run complete', f"Overall: {p.get('overall_status', 'unknown')}"]
    for c, r in p.get('clients', {}).items():
        lines.append(f"{c}: {r.get('status')} files={r.get('files_checked', 0)} failed={r.get('files_failed', 0)} size={r.get('backup_size_human', 'unknown')} latest={r.get('latest_backup_time', 'unknown')}")
    dh = p.get('disk_health', {})
    lines.append(f"Disk health: {dh.get('status')} drives={dh.get('drives_checked', 0)} failed={dh.get('drives_failed', 0)}")
    q = p.get('qnap_health', {})
    lines.append(f"QNAP: {q.get('status')}")
    b2 = p.get('b2', {})
    if b2:
        lines.append(f"Backblaze B2: {b2.get('status')} bucket={b2.get('bucket_size_human', 'unknown')} coverage={b2.get('offsite_coverage_score', 'unknown')}")
    if p.get('overall_status') != 'verified':
        for c, r in p.get('clients', {}).items():
            for e in r.get('errors', [])[:5]:
                lines.append(f'{c} error: {e}')
            for w in r.get('warnings', [])[:5]:
                lines.append(f'{c} warning: {w}')
        for d in dh.get('drives', []):
            if d.get('status') != 'healthy':
                lines.append(f"Drive {d.get('name')}: {d.get('status')} temp={d.get('temperature')} realloc={d.get('reallocated')} pending={d.get('pending')}")
    return '\n'.join(lines)[:3900]
