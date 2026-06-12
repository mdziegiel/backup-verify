# Backup Verify

Self-hosted backup verification, restore-drill, notification, offsite, and disk-health dashboard for the MRDTech homelab.

It does the part most backup systems avoid saying out loud: it checks whether the backup looks restorable. A backup that cannot be read, sampled, mounted, or validated is not a backup. It is a ritual with a progress bar.

## Features

- Single Docker container on port `10122`.
- Dashboard schedule management with enable/disable, daily/weekly/monthly visual cron builder, next-run display, per-client Run Now, and Run All Clients.
- Persistent SQLite history for all verification runs, client details, disk metrics, notification attempts, trend data, and exports.
- Clients Management UI backed by SQLite: add, edit, enable/disable, or remove backup clients without editing config files or restarting the container.
- UrBackup latest-backup discovery for configured clients.
- File backup spot checks with configurable random sample size.
- Critical path verification for `Windows`, `Users`, `Program Files`, and `ProgramData` by default.
- Backup-age warning threshold, default 2 days.
- Per-file failure details showing the exact file and reason.
- Backup size tracking over time with warning when size drops significantly.
- Restore drill that copies a sample file to a temp location and verifies it can be opened/read.
- Retention policy checker for minimum backup copy count.
- VHD/VHDX/raw image detection with `qemu-img` metadata/readability checks and guestmount capability reporting.
- SMART history trending, drive temperature tracking, NVMe-specific health fields, and predictive warnings for bad SMART indicators.
- QNAP API probe for `10.10.10.230` volume-health reachability.
- Notification settings panel for Telegram, Email/SMTP, and Gotify with test buttons.
- Notification triggers: completion, failure-only, warning, disk-health-change flag, and quiet hours.
- Backblaze B2 panel with API/bucket settings, bucket size, estimated monthly cost, latest uploaded object time, sync-behind warning, download test, SHA1 metadata accounting, and offsite coverage estimate.
- Dashboard improvements: prominent last successful backup date per client, green/yellow/red age coloring, 30-day success rate, detailed client view, backup trend chart, disk cards, history table.
- Weekly summary support through the normal scheduler and notification channels.
- NOC integration output at `/opt/backup-verify/results.json`.

## Target environment

- Host: `10.10.10.76`
- UI: `http://10.10.10.76:10122`
- UrBackup: `http://10.10.10.76:55414`
- Backup storage: `/mnt/qnap-backups/urbackup` mounted read-only
- QNAP: `https://10.10.10.230`
- Proxmox: `https://10.10.10.251:8006`
- Initial clients: `MichaelD-ASUS`, `MichaelD-Lenovo`, seeded into SQLite on first start and then managed from the dashboard.

## NOC Dashboard output

After each run the app writes `/opt/backup-verify/results.json`:

```json
{
  "MichaelD-ASUS": {
    "status": "verified",
    "last_checked": "2026-06-11T02:00:00",
    "files_checked": 87,
    "files_failed": 0
  },
  "disk_health": {
    "status": "healthy",
    "drives_checked": 4,
    "drives_failed": 0
  },
  "b2": {
    "status": "healthy",
    "last_run_time": "2026-06-11T02:00:00",
    "offsite_coverage_score": 96.4
  }
}
```

## Quick start

```bash
git clone git@github.com:mdziegiel/backup-verify.git
cd backup-verify
cp .env.example .env
$EDITOR .env
docker compose up -d --build
curl http://127.0.0.1:10122/api/health
```

## API

- `GET /api/health`
- `GET /api/status`
- `GET /api/history`
- `GET /api/history.csv`
- `GET /api/history.pdf`
- `GET /api/results.json`
- `GET /api/clients`
- `POST /api/clients`
- `PUT /api/clients/{id}`
- `DELETE /api/clients/{id}`
- `POST /api/run` with optional `{"clients":["MichaelD-ASUS"]}`
- `POST /api/settings`
- `POST /api/test-notification` with `{"channel":"telegram|email|gotify|all"}`

## Deployment notes

The compose file mounts:

- `backup_verify_data:/data` for SQLite.
- `backup_verify_results:/opt/backup-verify` for NOC results.
- `/mnt/qnap-backups/urbackup:/mnt/qnap-backups/urbackup:ro` for backup reads.
- `/dev:/dev:ro` plus `privileged: true` for SMART. SMART requires device access. Dashboards pretending otherwise are theater.
- `/home/michaeld/.hermes/.env:/host-hermes/.env:ro` for Telegram fallback. If the Hermes env only exists on another host, set the token/chat ID directly in `.env` on `10.10.10.76` or mount it properly. Cross-host magic is not a transport protocol.

## Local development

No Python packages are required.

```bash
python3 -m unittest discover -s tests
APP_DATA_DIR=/tmp/backup-verify APP_DB=/tmp/backup-verify/app.db RESULTS_FILE=/tmp/backup-verify/results.json APP_PORT=10122 python3 -m backup_verify.server
```

## Security

- Keep `.env` out of Git.
- Keep port `10122` LAN/VPN-only.
- Mount backup storage read-only.
- Use read-only API credentials where possible.
- Treat the host running SMART checks as privileged.
- B2, SMTP, Gotify, and Telegram secrets are stored in the persistent app settings database if saved through the dashboard. That is convenient. It is also a secret store, whether anyone admits it or not.

## License

MIT
