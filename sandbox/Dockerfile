# One image for the game containers and the egress proxy.
# Build from the repo root:  docker compose -f sandbox/compose.yaml build
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY core ./core
COPY ports ./ports
COPY adapters ./adapters
COPY harness ./harness
COPY analysis ./analysis
COPY sandbox ./sandbox
COPY config ./config

RUN useradd --create-home --uid 10001 player
USER player
