import json,urllib.parse,urllib.request
def send_telegram(settings,text):
    if not settings.telegram_bot_token or not settings.telegram_chat_id: return {'sent':False,'reason':'telegram not configured'}
    data=urllib.parse.urlencode({'chat_id':settings.telegram_chat_id,'text':text[:3900],'disable_web_page_preview':'true'}).encode()
    with urllib.request.urlopen(urllib.request.Request(f'https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage',data=data,headers={'Content-Type':'application/x-www-form-urlencoded'}),timeout=15) as r: body=r.read().decode()
    try: return json.loads(body)
    except Exception: return {'sent':True,'raw':body[:200]}
def format_summary(p):
    lines=['Backup Verify run complete',f"Overall: {p.get('overall_status','unknown')}"]
    for c,r in p.get('clients',{}).items(): lines.append(f"{c}: {r.get('status')} files={r.get('files_checked',0)} failed={r.get('files_failed',0)}")
    dh=p.get('disk_health',{}); lines.append(f"Disk health: {dh.get('status')} drives={dh.get('drives_checked',0)} failed={dh.get('drives_failed',0)}")
    q=p.get('qnap_health',{}); lines.append(f"QNAP: {q.get('status')}")
    if p.get('overall_status')!='verified':
        for c,r in p.get('clients',{}).items():
            for e in r.get('errors',[])[:5]: lines.append(f'{c} error: {e}')
            for w in r.get('warnings',[])[:3]: lines.append(f'{c} warning: {w}')
        for d in dh.get('drives',[]):
            if d.get('status')!='healthy': lines.append(f"Drive {d.get('name')}: {d.get('status')} temp={d.get('temperature')} realloc={d.get('reallocated')} pending={d.get('pending')}")
    return '\n'.join(lines)[:3900]
