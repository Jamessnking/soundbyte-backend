# SoundByte audio processing backend — Render.com ready.
# Uses python:3.11-slim + ffmpeg from Debian repos for a small, reliable image.
FROM python:3.11-slim

# 1. System deps: ffmpeg for audio processing, build-essential for wheels
#    that occasionally need to compile on arm64.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# 2. Python deps
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 3. App source
COPY . .

# 4. Render injects PORT; default to 8001 locally.
ENV PORT=8001
EXPOSE 8001

# 5. Bind to 0.0.0.0 and honour Render's PORT env.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8001}"]
