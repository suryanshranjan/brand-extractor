FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    BRANDSCAN_DATA_DIR=/data PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY brandscan ./brandscan
RUN pip install ".[server]" && python -m spacy download en_core_web_sm

RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app /data
USER app
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["brandscan", "serve", "--host", "0.0.0.0", "--port", "8000"]
