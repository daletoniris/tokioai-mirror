FROM python:3.12-slim

LABEL maintainer="TokioAI"
LABEL version="2.0.0"

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget git openssh-client jq vim nano cron \
    gnupg apt-transport-https ca-certificates \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install gcloud CLI (for GCP tools)
RUN curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update && apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt requests

# Copy application
COPY . .

# Install as package (enables `tokio` command)
RUN pip install --no-cache-dir -e .

# Workspace volume
RUN mkdir -p /workspace/cli /root/.ssh
VOLUME /workspace

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default: start API server
CMD ["tokio", "server"]
