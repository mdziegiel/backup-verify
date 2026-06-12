const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => (s ?? '').toString().replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
const cls = s => (s || 'unknown').toLowerCase();
const metric = (k,v) => `<div class="metric"><span>${esc(k)}</span><strong>${esc(v ?? '—')}</strong></div>`;
let STATE = null;
let TEST_RESULTS = {};

const PROVIDERS = {
  backblaze: {
    label: 'Backblaze B2', short: 'B2', icon: '🔥', accent: '#f97316',
    description: 'Object Lock capable B2 bucket validation, freshness threshold, sample download evidence.',
    fields: [
      ['keyId','Key ID','password'], ['applicationKey','Application Key','password'], ['bucketName','Bucket Name','text'], ['behindDays','Behind threshold days','number']
    ]
  },
  s3: {
    label: 'Amazon S3 / S3-Compatible', short: 'AWS', icon: '◆', accent: '#ff9900',
    description: 'AWS S3, MinIO, Wasabi, DigitalOcean Spaces, or any SigV4-compatible object target.',
    fields: [
      ['accessKeyId','Access Key ID','password'], ['secretAccessKey','Secret Access Key','password'], ['bucket','Bucket','text'], ['region','Region','text'], ['endpointUrl','Endpoint URL','url']
    ]
  },
  azure: {
    label: 'Azure Blob Storage', short: 'AZ', icon: '▰', accent: '#38bdf8',
    description: 'Azure storage account container target with access-key based validation.',
    fields: [['accountName','Storage Account Name','text'], ['accountKey','Access Key','password'], ['containerName','Container Name','text']]
  },
  gcs: {
    label: 'Google Cloud Storage', short: 'GCP', icon: '◉', accent: '#22c55e',
    description: 'GCS bucket target using service account JSON credentials.',
    fields: [['serviceAccountJson','Service Account JSON','textarea'], ['bucketName','Bucket Name','text']]
  },
  sftp: {
    label: 'SFTP/SSH', short: 'SSH', icon: '⌁', accent: '#a78bfa',
    description: 'Hardened SSH/SFTP offsite repository with key or password authentication.',
    fields: [['host','Host','text'], ['port','Port','number'], ['username','Username','text'], ['privateKey','Private Key','textarea'], ['password','Password','password'], ['remotePath','Remote Path','text']]
  },
  rclone: {
    label: 'rclone Remote', short: 'RCL', icon: '⟳', accent: '#60a5fa',
    description: 'Any backend already configured in rclone: Google Drive, Dropbox, OneDrive, and worse ideas.',
    fields: [['remoteName','Remote name','text'], ['path','Path','text']]
  },
  webdav: {
    label: 'Generic WebDAV', short: 'DAV', icon: '▣', accent: '#14b8a6',
    description: 'Standards-based WebDAV target with optional basic authentication.',
    fields: [['url','URL','url'], ['username','Username','text'], ['password','Password','password'], ['remotePath','Remote Path','text']]
  }
};

const SECRET_FIELDS = new Set(['keyId','applicationKey','accessKeyId','secretAccessKey','accountKey','serviceAccountJson','privateKey','password','username']);

async function post(url, body){
  const r = await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  return r.json();
}
function ageClass(t){
  if(!t) return 'unknown';
  const days = (Date.now() - new Date(t).getTime()) / 86400000;
  return days <= 2 ? 'verified' : (days <= 7 ? 'warning' : 'failed');
}
function chart(points){
  if(!points || points.length < 2) return '<p class="tiny">No trend yet.</p>';
  const vals = points.map(p => Number(p.size_bytes || 0));
  const max = Math.max(...vals,1);
  const xy = vals.map((v,i)=>`${(i/(vals.length-1))*100},${40-(v/max)*36}`).join(' ');
  return `<svg class="chart" viewBox="0 0 100 42" preserveAspectRatio="none"><polyline points="${xy}" /></svg>`;
}
function tempChart(points){
  const rows = (points || []).map(p => Number(p.temperature)).filter(v => !Number.isNaN(v) && v > 0).slice(-30);
  if(rows.length < 2) return '<p class="tiny">No temperature trend yet.</p>';
  const min = Math.min(...rows,20), max = Math.max(...rows,60);
  const span = Math.max(1, max-min);
  const xy = rows.map((v,i)=>`${(i/(rows.length-1))*100},${40-((v-min)/span)*36}`).join(' ');
  return `<svg class="chart temp" viewBox="0 0 100 42" preserveAspectRatio="none"><polyline points="${xy}" /></svg><p class="tiny">Temperature trend ${Math.min(...rows)}°C-${Math.max(...rows)}°C</p>`;
}
function formData(form){
  const o = {};
  [...new FormData(form).entries()].forEach(([k,v]) => o[k]=v);
  form.querySelectorAll('input[type=checkbox]').forEach(i => o[i.name] = i.checked);
  return o;
}
function fillForm(id, s){
  const f = $(id); if(!f) return;
  Object.entries(s).forEach(([k,v]) => {
    const el = f.elements[k]; if(!el) return;
    if(el.type === 'checkbox') el.checked = !!v && v !== 'false';
    else if(v && v !== 'configured') el.value = Array.isArray(v) ? v.join(',') : v;
  });
}
function wireForm(id){
  const f = $(id); if(!f) return;
  f.addEventListener('submit', async e => {
    e.preventDefault();
    const data = formData(f);
    Object.keys(data).forEach(k => { if(data[k] === 'configured') delete data[k]; });
    const res = await post('/api/settings', data);
    toast(res.ok ? 'Saved.' : JSON.stringify(res));
    await load();
  });
}
function toast(msg){
  let t = $('#toast');
  if(!t){ t = document.createElement('div'); t.id='toast'; document.body.appendChild(t); }
  t.textContent = msg; t.className='show'; setTimeout(()=>t.className='',2500);
}
function uid(){ return 'offsite-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2,8); }
function bytes(n){
  n = Number(n || 0); const u = ['B','KiB','MiB','GiB','TiB','PiB']; let i=0;
  while(n >= 1024 && i < u.length-1){ n/=1024; i++; }
  return i ? `${n.toFixed(1)} ${u[i]}` : `${n} B`;
}
function fmtTime(t){ return t ? new Date(t).toLocaleString() : 'Never'; }
function statusIcon(status){
  if(['healthy','verified','current','success'].includes(cls(status))) return '✓';
  if(['warning','disabled','unknown'].includes(cls(status))) return '⚠';
  return '✕';
}
function providerDefaults(type){
  const def = PROVIDERS[type];
  const cfg = {};
  def.fields.forEach(([key,,kind]) => cfg[key] = key === 'port' ? '22' : (key === 'region' ? 'us-east-1' : (key === 'behindDays' ? '2' : '')));
  return { id: uid(), type, name: def.label, enabled: false, expanded: true, config: cfg };
}
function providerStatus(provider, latest){
  const test = TEST_RESULTS[provider.id];
  if(test) return { status: test.status || (test.ok ? 'healthy' : 'failed'), message: test.message, checked_at: test.checked_at };
  if(provider.type === 'backblaze' && latest?.b2){
    return { status: latest.b2.status || 'unknown', message: (latest.b2.warnings||[])[0] || latest.b2.sync_status || 'No B2 warnings.', checked_at: latest.b2.last_run_time };
  }
  return { status: provider.enabled ? 'unknown' : 'disabled', message: provider.enabled ? 'Awaiting connection test or next sync.' : 'Provider disabled. Settings preserved.', checked_at: null };
}
function providerMetrics(provider, latest){
  if(provider.type === 'backblaze' && latest?.b2){
    const b2 = latest.b2;
    return { lastStatus: b2.sync_status || b2.status || 'unknown', lastTime: b2.last_run_time, stored: b2.bytes_uploaded || 0, storedHuman: b2.bucket_size_human || bytes(b2.bytes_uploaded), health: b2.status || 'unknown' };
  }
  const test = TEST_RESULTS[provider.id];
  return { lastStatus: test ? test.status : (provider.enabled ? 'not synced yet' : 'disabled'), lastTime: test?.checked_at, stored: 0, storedHuman: '—', health: test?.status || (provider.enabled ? 'unknown' : 'disabled') };
}
function configuredProviders(){ return ((STATE?.settings?.offsite_providers) || []).filter(p => p && p.type && PROVIDERS[p.type]); }
function offsiteSummary(providers, latest){
  const metrics = providers.map(p => providerMetrics(p, latest));
  const active = providers.filter(p => p.enabled);
  const healthy = providers.filter(p => p.enabled && ['healthy','verified','current','success'].includes(cls(providerStatus(p, latest).status))).length;
  const totalBytes = metrics.reduce((a,m)=>a + Number(m.stored || 0), 0);
  const times = metrics.map(m => m.lastTime).filter(Boolean).sort();
  const last = times.length ? times[times.length-1] : null;
  const bannerClass = active.length === 0 ? 'unknown' : (healthy === active.length ? 'healthy' : (healthy ? 'warning' : 'failed'));
  return `<div class="offsite-health ${bannerClass}">
    <div class="health-orb">${statusIcon(bannerClass)}</div>
    <div><span>Offsite health</span><strong>${healthy} of ${active.length} providers healthy</strong><p>${active.length ? 'Independent provider telemetry aggregated from configured targets.' : 'No enabled providers. Offsite redundancy is currently a theory.'}</p></div>
    <div class="health-metrics">${metric('Last sync across all providers', fmtTime(last))}${metric('Total bytes stored offsite', totalBytes ? bytes(totalBytes) : '—')}${metric('Configured providers', providers.length)}</div>
  </div>`;
}
function renderOffsite(){
  if(!STATE) return;
  const latest = STATE.latest || {};
  let providers = configuredProviders();
  $('#offsiteSummary').innerHTML = offsiteSummary(providers, latest);
  const wrap = $('#offsiteProviders');
  if(!providers.length){
    wrap.innerHTML = `<div class="empty-offsite"><div class="empty-icon">☁</div><h3>Add your first offsite provider</h3><p>Build a real 3-2-1 backup posture with B2, S3, Azure, GCS, SFTP, rclone, or WebDAV targets.</p><button type="button" onclick="addProvider()">Add your first offsite provider</button></div>`;
    return;
  }
  wrap.innerHTML = providers.map(renderProviderCard).join('');
  wireOffsiteCards();
}
function renderProviderCard(provider){
  const def = PROVIDERS[provider.type];
  const ps = providerStatus(provider, STATE.latest || {});
  const pm = providerMetrics(provider, STATE.latest || {});
  const disabled = provider.enabled ? '' : ' disabled';
  const expanded = provider.expanded ? ' expanded' : '';
  const fields = def.fields.map(([key,label,kind]) => {
    const value = provider.config?.[key] ?? '';
    if(kind === 'textarea') return `<label>${esc(label)}<textarea data-provider-field="${esc(key)}" ${!provider.enabled?'disabled':''} placeholder="${SECRET_FIELDS.has(key) && value === 'configured' ? 'configured' : ''}">${value === 'configured' ? 'configured' : esc(value)}</textarea></label>`;
    return `<label>${esc(label)}<input data-provider-field="${esc(key)}" type="${kind}" value="${value === 'configured' ? 'configured' : esc(value)}" ${!provider.enabled?'disabled':''}></label>`;
  }).join('');
  return `<article class="provider-card${disabled}${expanded}" data-provider-id="${esc(provider.id)}">
    <div class="provider-head">
      <button class="provider-collapse" type="button" data-action="toggle-card" aria-label="Expand ${esc(def.label)}">⌄</button>
      <div class="provider-logo" style="--accent:${def.accent}"><span>${def.icon}</span><small>${esc(def.short)}</small></div>
      <div class="provider-title"><h3>${esc(provider.name || def.label)}</h3><p>${esc(def.description)}</p></div>
      <div class="provider-badges"><span class="status ${cls(ps.status)}"><b>${statusIcon(ps.status)}</b>${esc(ps.status || 'unknown')}</span><label class="switch"><input type="checkbox" data-action="toggle-enabled" ${provider.enabled?'checked':''}><span></span></label></div>
    </div>
    <div class="provider-body">
      <div class="provider-telemetry">
        ${metric('Last sync status', pm.lastStatus)}${metric('Last sync time', fmtTime(pm.lastTime))}${metric('Bytes stored', pm.storedHuman)}
        <div class="metric"><span>Sync health</span><strong class="health-dot ${cls(pm.health)}">${statusIcon(pm.health)} ${esc(pm.health)}</strong></div>
      </div>
      <div class="provider-message ${cls(ps.status)}">${esc(ps.message || 'No telemetry yet.')}</div>
      <div class="provider-form">${fields}</div>
      <div class="provider-actions"><button type="button" data-action="save-provider">Save provider</button><button type="button" data-action="test-provider">Test Connection</button><button type="button" class="danger" data-action="remove-provider">Remove provider</button></div>
    </div>
  </article>`;
}
function cardProvider(card){
  const id = card.dataset.providerId;
  const p = configuredProviders().find(x => x.id === id);
  if(!p) return null;
  const next = JSON.parse(JSON.stringify(p));
  card.querySelectorAll('[data-provider-field]').forEach(el => next.config[el.dataset.providerField] = el.value);
  next.enabled = !!card.querySelector('[data-action="toggle-enabled"]')?.checked;
  next.expanded = card.classList.contains('expanded');
  return next;
}
async function saveProviders(providers, quiet=false){
  const res = await post('/api/settings', {offsite_providers: providers});
  if(!res.ok){ toast('Save failed: ' + JSON.stringify(res).slice(0,160)); return false; }
  if(!quiet) toast('Offsite providers saved.');
  await load();
  return true;
}
async function saveProvider(card, quiet=false){
  const updated = cardProvider(card); if(!updated) return false;
  const providers = configuredProviders().map(p => p.id === updated.id ? updated : p);
  return saveProviders(providers, quiet);
}
function wireOffsiteCards(){
  $$('.provider-card').forEach(card => {
    card.querySelector('[data-action="toggle-card"]')?.addEventListener('click', async () => {
      card.classList.toggle('expanded');
      const p = cardProvider(card); const providers = configuredProviders().map(x => x.id === p.id ? p : x);
      await saveProviders(providers, true);
    });
    card.querySelector('[data-action="toggle-enabled"]')?.addEventListener('change', async e => {
      card.classList.toggle('disabled', !e.target.checked);
      card.querySelectorAll('[data-provider-field]').forEach(f => f.disabled = !e.target.checked);
      await saveProvider(card, true);
    });
    card.querySelector('[data-action="save-provider"]')?.addEventListener('click', () => saveProvider(card));
    card.querySelector('[data-action="remove-provider"]')?.addEventListener('click', async () => {
      const p = cardProvider(card);
      if(!confirm(`Remove ${p.name || PROVIDERS[p.type].label}? Settings for this provider will be deleted.`)) return;
      await saveProviders(configuredProviders().filter(x => x.id !== p.id));
    });
    card.querySelector('[data-action="test-provider"]')?.addEventListener('click', async e => {
      e.target.classList.add('loading'); e.target.textContent = 'Testing…';
      await saveProvider(card, true);
      const p = cardProvider(card);
      const res = await post('/api/test-offsite', {provider:p});
      TEST_RESULTS[p.id] = res;
      toast((res.ok ? 'Connection verified: ' : 'Connection failed: ') + (res.message || '').slice(0,130));
      e.target.classList.remove('loading'); e.target.textContent = 'Test Connection';
      await load();
    });
  });
}
async function addProvider(type){
  type = type || $('#providerType')?.value || 'backblaze';
  const providers = configuredProviders();
  providers.push(providerDefaults(type));
  await saveProviders(providers);
}
function setupProviderSelect(){
  const sel = $('#providerType'); if(!sel) return;
  sel.innerHTML = Object.entries(PROVIDERS).map(([k,v]) => `<option value="${k}">${esc(v.label)}</option>`).join('');
  $('#addProvider')?.addEventListener('click', () => addProvider(sel.value));
}

function resetClientForm(){
  const f = $('#clientForm'); if(!f) return;
  f.reset(); f.elements.id.value=''; f.elements.backup_root.value='/mnt/qnap-backups/urbackup'; f.elements.sample_size.value='87'; f.elements.backup_age_threshold_days.value='2'; f.elements.enabled.checked=true; $('#clientFormTitle').textContent='Add client';
}
function renderClientAdmin(){
  const el = $('#clientAdmin'); if(!el || !STATE) return;
  const clients = STATE.client_configs || [];
  el.innerHTML = clients.map(c => `<div class="client-admin-row" data-client-id="${c.id}"><div><b>${esc(c.name)}</b><span>${esc(c.urbackup_client_name || c.name)} · ${esc(c.backup_root)}</span><small>sample ${esc(c.sample_size)} · age warn ${esc(c.backup_age_threshold_days)} days · ${c.enabled?'enabled':'disabled'}</small></div><div class="row-actions"><button type="button" data-client-action="edit">Edit</button><button type="button" data-client-action="toggle">${c.enabled?'Disable':'Enable'}</button><button type="button" class="danger" data-client-action="remove">Remove</button></div></div>`).join('') || '<p class="tiny">No clients configured. Add one before trusting the scheduler. Obviously.</p>';
  el.querySelectorAll('[data-client-action]').forEach(btn => btn.addEventListener('click', async () => {
    const id = Number(btn.closest('[data-client-id]').dataset.clientId);
    const c = (STATE.client_configs||[]).find(x => Number(x.id)===id);
    if(!c) return;
    const action = btn.dataset.clientAction;
    if(action === 'edit'){
      const f = $('#clientForm'); $('#clientFormTitle').textContent='Edit client';
      ['id','name','backup_root','urbackup_client_name','sample_size','backup_age_threshold_days'].forEach(k => f.elements[k].value = c[k] ?? '');
      f.elements.enabled.checked = !!c.enabled; f.scrollIntoView({behavior:'smooth',block:'center'}); return;
    }
    if(action === 'toggle'){
      const res = await fetch('/api/clients/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({...c,enabled:!c.enabled})}).then(r=>r.json());
      toast(res.ok ? 'Client updated.' : JSON.stringify(res)); await load(); return;
    }
    if(action === 'remove'){
      if(!confirm(`Remove client ${c.name}? Historical run data stays; configuration is deleted.`)) return;
      const res = await fetch('/api/clients/'+id,{method:'DELETE'}).then(r=>r.json());
      toast(res.ok ? 'Client removed.' : JSON.stringify(res)); await load();
    }
  }));
}
function setupClientAdmin(){
  $('#newClient')?.addEventListener('click', resetClientForm);
  $('#cancelClientEdit')?.addEventListener('click', resetClientForm);
  $('#clientForm')?.addEventListener('submit', async e => {
    e.preventDefault(); const data = formData(e.target); const id = data.id; delete data.id;
    const res = await fetch(id ? '/api/clients/'+id : '/api/clients', {method:id?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(r=>r.json());
    toast(res.ok ? 'Client saved.' : JSON.stringify(res)); resetClientForm(); await load();
  });
}

async function load(){
  const [status,hist] = await Promise.all([fetch('/api/status').then(r=>r.json()), fetch('/api/history').then(r=>r.json())]);
  STATE = status;
  const latest = status.latest || {};
  const settings = status.settings || {};
  $('#overall').textContent = latest.overall_status || (status.running ? 'running' : 'unknown');
  $('#overall').className = 'status ' + cls(latest.overall_status || (status.running ? 'running' : 'unknown'));
  $('#nextRun').textContent = status.next_run || 'disabled';
  $('#running').textContent = status.running ? 'yes' : 'no';
  $('#backupRoot').textContent = settings.backup_root || '—';
  ['#scheduleForm','#verifyForm','#telegramForm','#emailForm','#weeklySummaryForm','#gotifyForm','#triggerForm'].forEach(id => fillForm(id, settings));

  const clients = latest.clients || {};
  const summaries = status.clients || {};
  const clientNames = Object.keys(summaries).length ? Object.keys(summaries) : (settings.clients || []);
  $('#clients').innerHTML = clientNames.map(name => {
    const r = clients[name] || {};
    const sum = summaries[name] || {};
    const failures = (r.file_failures || []).slice(0,8).map(f => `<li>${esc(f.file)} — ${esc(f.reason)}</li>`).join('');
    return `<article class="card client" data-client="${esc(name)}">
      <div class="client-head"><h3>${esc(name)}</h3><div class="status ${cls(r.status || sum.last_status)}">${esc(r.status || sum.last_status || 'unknown')}</div></div>
      <div class="last-success ${ageClass(sum.last_successful_backup)}">Last successful backup: ${esc(sum.last_successful_backup || 'never')}</div>
      <div class="metrics">${metric('Last run', sum.last_run_time)}${metric('Next run', status.next_run || 'disabled')}${metric('Success rate 30d', sum.success_rate_30d == null ? '—' : sum.success_rate_30d + '%')}${metric('Backup age', r.backup_age_days == null ? '—' : r.backup_age_days + ' days')}${metric('Backup size', r.backup_size_human)}${metric('Files checked', r.files_checked)}${metric('Files failed', r.files_failed)}${metric('Retention copies', r.retention_copies_found)}</div>
      <button onclick="runClient('${esc(name)}')">Run now</button>
      <details><summary>Detailed client view</summary>
        <h4>Warnings</h4><p class="tiny">${(r.warnings||[]).map(esc).join('<br>') || 'None'}</p>
        <h4>Per-file failures</h4><ul class="tiny">${failures || '<li>None</li>'}</ul>
        <h4>Restore drill</h4><pre>${esc(JSON.stringify(r.restore_drill || {}, null, 2))}</pre>
        <h4>Image mount/readability checks</h4><pre>${esc(JSON.stringify(r.image_checks || [], null, 2))}</pre>
        <h4>Backup trend</h4>${chart(sum.trend)}
      </details>
    </article>`;
  }).join('') || '<p class="tiny">No clients configured.</p>';

  const dh = latest.disk_health || {};
  const trends = status.disk_trends || {};
  $('#disks').innerHTML = `<article class="card"><h3>Summary</h3><div class="status ${cls(dh.status)}">${esc(dh.status || 'unknown')}</div>${metric('Drives checked', dh.drives_checked)}${metric('Drives failed', dh.drives_failed)}</article>` +
    (dh.drives || []).map(d => `<article class="card"><h3>${esc(d.name)}</h3><div class="status ${cls(d.status)}">${esc(d.status)}</div>${metric('Type', d.type)}${metric('Temp', d.temperature == null ? '—' : d.temperature + '°C')}${metric('Reallocated', d.reallocated)}${metric('Pending', d.pending)}${metric('Uncorrectable/media errors', d.uncorrectable)}${metric('NVMe used', d.percentage_used == null ? '—' : d.percentage_used + '%')}<h4>Drive temperature graph</h4>${tempChart(trends[d.name] || [])}<p class="tiny">${(d.warnings||[]).map(esc).join('<br>')}</p></article>`).join('');

  const qh = latest.qnap_health || {};
  $('#qnap').innerHTML = `<article class="card"><h3>10.10.10.230</h3><div class="status ${cls(qh.status)}">${esc(qh.status || 'unknown')}</div>${(qh.volumes||[]).map(v => `<div class="metric"><span>${esc(v.name || 'volume')}</span><strong>${esc(v.status || 'unknown')}</strong></div>`).join('') || '<p class="tiny">No QNAP volume telemetry returned.</p>'}<p class="tiny">${(qh.warnings||[]).map(esc).join('<br>') || 'No warnings.'}</p></article>`;

  renderClientAdmin();
  renderOffsite();
  $('#history').innerHTML = `<table><thead><tr><th>ID</th><th>Started</th><th>Status</th><th>Summary</th></tr></thead><tbody>${(hist.runs||[]).map(r => `<tr><td>${r.id}</td><td>${esc(r.started_at)}</td><td><span class="status ${cls(r.status)}">${esc(r.status)}</span></td><td>${esc(r.summary)}</td></tr>`).join('')}</tbody></table>`;
}

async function runClient(name){ toast('Starting ' + name); await post('/api/run',{clients:[name]}); setTimeout(load,1200); }
$('#runAll')?.addEventListener('click', async () => { toast('Starting all clients'); await post('/api/run',{}); setTimeout(load,1200); });
$('#refresh')?.addEventListener('click', load);
$$('[data-test]').forEach(b => b.addEventListener('click', async () => { const r = await post('/api/test-notification',{channel:b.dataset.test}); toast(JSON.stringify(r).slice(0,160)); }));
$('#testWeeklySummary')?.addEventListener('click', async () => { const r = await post('/api/weekly-summary',{force:true}); toast(JSON.stringify(r).slice(0,180)); });
['#scheduleForm','#verifyForm','#telegramForm','#emailForm','#weeklySummaryForm','#gotifyForm','#triggerForm'].forEach(wireForm);
setupClientAdmin();
setupProviderSelect();
load();
setInterval(load, 30000);
