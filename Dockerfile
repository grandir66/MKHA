FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for ping
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY scripts/ scripts/

EXPOSE 8080

CMD ["python", "-m", "src.main", "-c", "config/ha_config.yaml"]
