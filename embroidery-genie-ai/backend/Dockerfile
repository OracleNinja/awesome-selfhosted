# Embroidery Genie AI — backend
#
# Multi-stage: wheels are built once, then copied into a slim runtime image.
FROM python:3.11-slim AS builder

WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


FROM python:3.11-slim

# libGL/libglib: OpenCV. potrace: higher-quality bitmap tracing (optional but
# recommended). fonts-dejavu-core: required for text/lettering designs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 libgl1 libglib2.0-0 potrace fonts-dejavu-core curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

WORKDIR /app
COPY . .

# Run unprivileged.
RUN useradd --create-home --uid 10001 genie \
    && mkdir -p /app/storage && chown -R genie /app
USER genie

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
