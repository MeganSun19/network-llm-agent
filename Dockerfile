FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for paramiko crypto extensions
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY services ./services
COPY tools ./tools
COPY examples ./examples
COPY scripts ./scripts
COPY config ./config
COPY web ./web

EXPOSE 5000

# Use gunicorn for production; bind app from web/app.py
CMD ["gunicorn", "--chdir", "web", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "app:app"]
