from datetime import datetime,timedelta
def vals(field,lo,hi):
    if field=='*': return set(range(lo,hi+1))
    out=set()
    for part in field.split(','):
        if part.startswith('*/'): out.update(range(lo,hi+1,int(part[2:])))
        else:
            try: out.add(int(part))
            except ValueError: pass
    return out
def next_run_from_cron(expr,tz):
    minute,hour,dom,mon,dow=(expr.split()+['*']*5)[:5]; mins=vals(minute,0,59); hours=vals(hour,0,23); dows=vals(dow,0,6) if dow!='*' else None
    cur=datetime.now(tz).replace(second=0,microsecond=0)+timedelta(minutes=1)
    for _ in range(366*24*60):
        cron_dow=(cur.weekday()+1)%7
        if cur.minute in mins and cur.hour in hours and (dows is None or cron_dow in dows): return cur
        cur+=timedelta(minutes=1)
    return cur
