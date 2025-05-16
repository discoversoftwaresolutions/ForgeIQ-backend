# agents/BuildSurfAgent/Dockerfile
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app
# Any system dependencies for LLM client libraries (e.g., for tokenizers, if not pure Python)
# RUN apt-get update && apt-get install -y --no-install-recommends some-lib && rm -rf /var/lib/apt/lists/*

COPY . /app

# Install Python dependencies from the root requirements.txt
# Ensure 'openai' (or other LLM client), 'httpx', 'redis' are in requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Command to run the agent's worker
CMD ["python", "-u", "agents/BuildSurfAgent/app/agent.py"]
