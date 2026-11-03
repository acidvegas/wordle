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
RUN mkdir -p /data

EXPOSE 20004

# Captchas live in process memory, so run a single gevent worker
CMD ["gunicorn", "--worker-class", "gevent", "--workers", "1", "--worker-connections", "1000", "--graceful-timeout", "5", "--bind", "0.0.0.0:20004", "app:app"]
