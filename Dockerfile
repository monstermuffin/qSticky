FROM python:slim

WORKDIR /app

COPY qsticky.py .
COPY requirements.txt .

RUN python -m pip install --no-cache-dir -r requirements.txt && \
    useradd -m appuser && \
    mkdir -p /tmp/health && \
    chown -R appuser:appuser /app /tmp/health && \
    chmod 755 /tmp/health

ENV HEALTH_FILE=/tmp/health/status.json

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD cat $HEALTH_FILE | grep -q '"healthy": true' || exit 1

CMD ["python", "qsticky.py"]