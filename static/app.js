const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => (s ?? '').toString().replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));
const cls = s => (s || 'unknown').toLowerCase();
const metric = (k,v) => `<div class="metric"><span>${esc(k)}</span><strong>${esc(v ?? '—')}</strong></div>`;
let STATE = null;

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
  ['#scheduleForm','#verifyForm','#telegramForm','#emailForm','#gotifyForm','#triggerForm','#b2Form'].forEach(id => fillForm(id, settings));

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
  $('#disks').innerHTML = `<article class="card"><h3>Summary</h3><div class="status ${cls(dh.status)}">${esc(dh.status || 'unknown')}</div>${metric('Drives checked', dh.drives_checked)}${metric('Drives failed', dh.drives_failed)}</article>` +
    (dh.drives || []).map(d => `<article class="card"><h3>${esc(d.name)}</h3><div class="status ${cls(d.status)}">${esc(d.status)}</div>${metric('Type', d.type)}${metric('Temp', d.temperature == null ? '—' : d.temperature + '°C')}${metric('Reallocated', d.reallocated)}${metric('Pending', d.pending)}${metric('Uncorrectable/media errors', d.uncorrectable)}${metric('NVMe used', d.percentage_used == null ? '—' : d.percentage_used + '%')}<p class="tiny">${(d.warnings||[]).map(esc).join('<br>')}</p></article>`).join('');

  const b2 = latest.b2 || {};
  $('#b2Status').innerHTML = `<h3>B2 status</h3><div class="status ${cls(b2.status)}">${esc(b2.status || 'unknown')}</div>${metric('Last job status', b2.last_backup_job_status)}${metric('Last run time', b2.last_run_time)}${metric('Bytes uploaded', b2.bucket_size_human)}${metric('Cost estimate', b2.cost_estimate_monthly_usd == null ? '—' : '$' + b2.cost_estimate_monthly_usd + '/mo')}${metric('Sync status', b2.sync_status)}${metric('Offsite coverage', b2.offsite_coverage_score == null ? '—' : b2.offsite_coverage_score + '%')}${metric('Download test', b2.download_test?.status)}<p class="tiny">${(b2.warnings||[]).map(esc).join('<br>')}</p>`;

  $('#history').innerHTML = `<table><thead><tr><th>ID</th><th>Started</th><th>Status</th><th>Summary</th></tr></thead><tbody>${(hist.runs||[]).map(r => `<tr><td>${r.id}</td><td>${esc(r.started_at)}</td><td><span class="status ${cls(r.status)}">${esc(r.status)}</span></td><td>${esc(r.summary)}</td></tr>`).join('')}</tbody></table>`;
}

async function runClient(name){ toast('Starting ' + name); await post('/api/run',{clients:[name]}); setTimeout(load,1200); }
$('#runAll')?.addEventListener('click', async () => { toast('Starting all clients'); await post('/api/run',{}); setTimeout(load,1200); });
$('#refresh')?.addEventListener('click', load);
$$('[data-test]').forEach(b => b.addEventListener('click', async () => { const r = await post('/api/test-notification',{channel:b.dataset.test}); toast(JSON.stringify(r).slice(0,160)); }));
['#scheduleForm','#verifyForm','#telegramForm','#emailForm','#gotifyForm','#triggerForm','#b2Form'].forEach(wireForm);
load();
setInterval(load, 30000);
