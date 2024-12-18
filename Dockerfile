FROM python:slim

WORKDIR /app

COPY qsticky.py .
COPY requirements.txt .

RUN python -m pip install --no-cache-dir -r requirements.txt && \
    mkdir -p /app/health && \
    touch /app/health/status.json && \
    chmod 666 /app/health/status.json && \
    chmod 777 /app/health

ENV HEALTH_FILE=/app/health/status.json

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD cat $HEALTH_FILE | grep -q '"healthy": true' || exit 1

CMD ["python", "qsticky.py"]