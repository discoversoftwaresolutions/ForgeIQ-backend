# agents/CacheAgent/Dockerfile
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app
# No special apt-get dependencies needed for this agent currently

COPY . /app

# Install Python dependencies from the root requirements.txt
# Ensure 'redis', 'fastapi', 'uvicorn[standard]', 'pydantic' are in requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Port FastAPI/Uvicorn will listen on inside the container. Railway's $PORT will map to this.
EXPOSE 8002 # Example port, can be configured via PORT env var for Uvicorn

# Command to run the FastAPI application using Uvicorn
# It points to the 'api_app' instance in 'agents/CacheAgent/app/agent.py'
# Railway will set the $PORT environment variable.
CMD ["uvicorn", "agents.CacheAgent.app.agent:api_app", "--host", "0.0.0.0", "--port", "8002"]
