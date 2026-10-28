FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY agentvideo ./agentvideo
RUN pip install --no-cache-dir ".[web]" \
    && useradd --create-home --uid 10001 agentvideo \
    && mkdir -p /data && chown agentvideo /data
USER agentvideo
ENV AGENTVIDEO_DATA=/data PORT=8000 HOST=0.0.0.0
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"
CMD ["agentvideo-web"]
