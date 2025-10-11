FROM python:3.13 AS builder

WORKDIR /install
COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.13-slim

ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=${GIT_COMMIT}

WORKDIR /app

COPY --from=builder /install /usr/local

COPY qsticky.py .

RUN mkdir -p /app/health && \
    touch /app/health/status.json && \
    chmod 666 /app/health/status.json && \
    chmod 777 /app/health

ENV HEALTH_FILE=/app/health/status.json

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD cat $HEALTH_FILE | grep -q '"healthy": true' || exit 1

CMD ["python", "qsticky.py"]