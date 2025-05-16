# agents/CI_CD_Agent/Dockerfile
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app

# Install Railway CLI
# Using the official Railway script for installation
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*
RUN bash <(curl -fsSL cli.new) --install-path /usr/local/bin
# Verify installation (optional, but good for debugging)
RUN railway --version

COPY . /app

# Install Python dependencies from the root requirements.txt
# Ensure 'redis', 'httpx' (if used) are in requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Command to run the agent's worker
CMD ["python", "-u", "agents/CI_CD_Agent/app/agent.py"]
