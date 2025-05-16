# agents/ReportingAgent/Dockerfile
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app
# No special apt-get dependencies needed for this agent currently

COPY . /app

# Install Python dependencies from the root requirements.txt
# Ensure 'redis' and any specific reporting client libs (e.g. boto3 for S3 if used) are in requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Command to run the agent's worker
CMD ["python", "-u", "agents/ReportingAgent/app/agent.py"]
