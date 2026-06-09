FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PYTHONIOENCODING=utf-8

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/codegraphcontext-requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /tmp/codegraphcontext-requirements.txt

COPY patch_cgc.py /tmp/patch_cgc.py
RUN python /tmp/patch_cgc.py && rm /tmp/patch_cgc.py

COPY watch_guard.py /usr/local/bin/cgc-watch-guard.py

ENTRYPOINT ["cgc"]
