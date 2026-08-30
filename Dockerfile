FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# System dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22+
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && node --version \
    && npm --version

WORKDIR /app

# Python dependencies
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir davey \
    && python -c "import davey; print('DAVEY INSTALL CHECK: OK')"

# BgUtil YouTube PO Token provider
RUN git clone --depth 1 --branch 1.3.1 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    /opt/bgutil-ytdlp-pot-provider

# Build PO Token provider
RUN cd /opt/bgutil-ytdlp-pot-provider/server \
    && npm ci \
    && npx tsc

# Copy bot files
COPY . .

# Start PO Token server + Discord bot
CMD ["sh", "-c", "node /opt/bgutil-ytdlp-pot-provider/server/build/main.js --port 4416 & python main.py"]
