# wordle - Developed by acidvegas in Python (https://github.com/acidvegas)
# Dockerfile

FROM python:3.12-alpine

WORKDIR /app

COPY requirements.txt .

# zope.interface (a gevent dependency) has no musl wheel, so build its pure-python version
RUN python3 -m pip install --upgrade pip && PURE_PYTHON=1 python3 -m pip install --no-cache-dir --only-binary :all: --no-binary zope.interface -r requirements.txt && rm requirements.txt

COPY wordle/ .

ENV WORDLE_DATA_DIR=/data
ENV PYTHONUNBUFFERED=1
# Run as an unprivileged user that owns only the data directory
RUN adduser -D -u 10001 app && mkdir -p /data && chown app:app /data

EXPOSE 20004

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD wget -qO- http://127.0.0.1:20004/healthz || exit 1

USER app

# Captchas live in process memory, so run a single gevent worker
CMD ["gunicorn", "--worker-class", "gevent", "--workers", "1", "--worker-connections", "1000", "--graceful-timeout", "5", "--bind", "0.0.0.0:20004", "app:app"]
