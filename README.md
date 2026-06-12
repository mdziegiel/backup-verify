# Backup Verify

Self-hosted backup verification and disk-health dashboard for the MRDTech homelab.

It does the part most backup systems avoid saying out loud: it checks whether the backup looks restorable. A backup that cannot be read, sampled, mounted, or validated is not a backup. It is a ritual.

## Features

- Single Docker container on port `10122`.
- Default schedule: Sunday at 02:00 (`0 2 * * 0`).
- UrBackup latest-backup discovery for configured clients.
- File backup spot checks with configurable random sample size.
- Verifies file existence, non-zero size, readability, critical Windows paths, and SHA checksum matches when checksum manifests exist.
- VHD/VHDX/raw image detection with `qemu-img` readability metadata checks. Full read-only mount checks require privileged container and host-supported image tooling.
- SMART health checks with `smartctl`: temperature, reallocated sectors, pending sectors, uncorrectable errors, power-on hours, and overall status.
- QNAP API probe for `10.10.10.230`.
- Telegram-only notifications, loaded from direct env vars or a mounted Hermes `.env`.
- NOC integration output at `/opt/backup-verify/results.json`.
- Dark glassmorphism web UI with dashboard, disk panel, history log, and manual trigger buttons.
- Persistent SQLite history volume.

## Target environment

- Host: `10.10.10.76`
- UI: `http://10.10.10.76:10122`
- UrBackup: `http://10.10.10.76:55414`
- Backup storage: `/mnt/qnap-backups/urbackup` mounted read-only
- QNAP: `https://10.10.10.230`
- Proxmox: `https://10.10.10.251:8006`
- Clients: `MichaelD-ASUS`, `MichaelD-Lenovo`

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
- `GET /api/results.json`
- `POST /api/run` with optional `{"clients":["MichaelD-ASUS"]}`
- `POST /api/settings`

## Deployment notes

The compose file mounts:

- `backup_verify_data:/data` for SQLite.
- `backup_verify_results:/opt/backup-verify` for NOC results.
- `/mnt/qnap-backups/urbackup:/mnt/qnap-backups/urbackup:ro` for backup reads.
- `/dev:/dev:ro` plus `privileged: true` for SMART. SMART requires device access. Dashboards pretending otherwise are theater.
- `/home/michaeld/.hermes/.env:/host-hermes/.env:ro` for Telegram fallback. If the Hermes env only exists on `10.10.10.237`, copy the Telegram token/chat ID into `.env` on `10.10.10.76` or mount it over the network. Cross-host magic is not a transport protocol.

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

## License

MIT
