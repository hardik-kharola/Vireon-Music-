FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && node --version \
    && npm --version

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Required by the bgutil yt-dlp provider plugin
RUN pip install --no-cache-dir "bgutil-ytdlp-pot-provider==1.3.1"

# Verify the plugin is installed and visible to Python
RUN python -c "import bgutil_ytdlp_pot_provider; print('BGUTIL PYTHON PLUGIN: OK')"

RUN git clone --depth 1 --branch 1.3.1 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    /opt/bgutil-ytdlp-pot-provider

RUN cd /opt/bgutil-ytdlp-pot-provider/server \
    && npm ci \
    && npx tsc

COPY . .

CMD ["sh", "-c", "node /opt/bgutil-ytdlp-pot-provider/server/build/main.js --port 4416 & exec python main.py"]
