FROM python:3.13-slim

# 系统依赖: ADB
RUN apt-get update && apt-get install -y --no-install-recommends \
    android-sdk-platform-tools \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY . .

# 可选: HDC (HarmonyOS) — 构建时若 hdc/hdc 存在则安装
RUN if [ -f hdc/hdc ]; then \
        cp hdc/hdc /usr/local/bin/hdc && chmod +x /usr/local/bin/hdc && rm -rf hdc; \
    fi

RUN mkdir -p /app/data

EXPOSE 8001
ENTRYPOINT ["bash", "docker-entrypoint.sh"]
