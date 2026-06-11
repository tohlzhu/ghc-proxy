# GHC Proxy image. Same image runs ROLE=proxy or ROLE=refresher.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# install deps first for layer caching
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# non-root
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8080

# default role is proxy; override ROLE=refresher for the worker workload
ENV ROLE=proxy
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,os,sys; \
    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('GHCPROXY_SERVER__PORT','8080')+'/healthz',timeout=3).status==200 else 1)" \
    || exit 1

CMD ["python", "-m", "ghcproxy"]
