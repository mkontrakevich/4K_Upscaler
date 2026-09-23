FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    MG4K_HEADLESS=1 \
    MG4K_RUNTIME_DIR=/runtime/bridge \
    MG4K_JOBS_ROOT=/tmp/mg4k-jobs \
    MG4K_HEALTH_PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN python -m py_compile \
      /app/v8_safe_appearance.py \
      /app/cloudflare_bridge.py \
      /app/cloud_processor_service.py \
    && useradd --create-home --uid 10001 mg4k \
    && mkdir -p /runtime/bridge /tmp/mg4k-jobs \
    && chown -R mg4k:mg4k /runtime /tmp/mg4k-jobs /app

USER mg4k

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()" || exit 1

CMD ["python", "cloud_processor_service.py"]
