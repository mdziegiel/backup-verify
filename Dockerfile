FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_HOST=0.0.0.0 APP_PORT=10122 APP_DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends smartmontools util-linux mount e2fsprogs dosfstools ntfs-3g qemu-utils ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backup_verify ./backup_verify
COPY static ./static
COPY README.md .env.example ./
RUN mkdir -p /data /opt/backup-verify
VOLUME ["/data", "/mnt/qnap-backups/urbackup", "/opt/backup-verify"]
EXPOSE 10122
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -m backup_verify.healthcheck
CMD ["python", "-m", "backup_verify.server"]
