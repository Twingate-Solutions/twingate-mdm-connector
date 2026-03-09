FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY src/ ./src/

# Copy the example config as the default; users should mount their own
# config.yaml via a Docker volume (see docker-compose.yml)
COPY config.yaml.example ./config.yaml

# Ensure Python output is sent straight to stdout/stderr (no buffering)
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.main"]
