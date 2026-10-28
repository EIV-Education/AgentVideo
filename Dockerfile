FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY agentvideo ./agentvideo
RUN pip install --no-cache-dir ".[web]"
ENV AGENTVIDEO_DATA=/data PORT=8000
VOLUME /data
EXPOSE 8000
CMD ["agentvideo-web"]
