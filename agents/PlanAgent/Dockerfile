FROM python:3.11-slim
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONPATH=/app
COPY . /app
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt
CMD ["python", "-u", "agents/PatchAgent/app/agent.py"]WORKDIR /app
COPY main.py .
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8020
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8020"]
# agents/CodeNavAgent/Dockerfile
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app
# For sentence-transformers, sometimes build tools are needed for underlying libraries
# For Hugging Face Hub interaction or model downloads
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY . /app

# Install Python dependencies from the root requirements.txt
# Ensure sentence-transformers, weaviate-client, fastapi, uvicorn are in requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# The port Uvicorn will listen on inside the container. Railway's $PORT will map to this.
EXPOSE 8001 

# Command to run the agent's API server
# It assumes agent.py when run with if __name__ == "__main__": calls run_worker() which starts the API server.
# The Uvicorn command is now inside agent.py's run_api_server method for this example.
# If you prefer to run Uvicorn directly here:
# CMD ["uvicorn", "agents.CodeNavAgent.app.agent_api:api_app", "--host", "0.0.0.0", "--port", "8001"]
# (This would require agent_api.py to define `api_app = FastAPI()` and import routes/logic from agent.py)
# For simplicity with the current agent.py structure:
CMD ["python", "-u", "agents/CodeNavAgent/app/agent.py"]
