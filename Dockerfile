FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BMS_DATA_DIR=/data

WORKDIR /app

COPY pyproject.toml README.md ./
COPY bmsweb ./bmsweb
RUN pip install --no-cache-dir .

# Runs as a normal user, so a stray write cannot land anywhere but the data volume.
RUN useradd --uid 10001 --create-home app \
    && mkdir -p /data \
    && chown -R app:app /data
USER app

EXPOSE 8000

# Uses the interpreter that is already here rather than pulling curl in for one request.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health').read()"

# 0.0.0.0 is right *inside* the container — what the service listens on beyond it is decided by
# the port mapping in docker-compose.yml, not here.
CMD ["uvicorn", "bmsweb.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
