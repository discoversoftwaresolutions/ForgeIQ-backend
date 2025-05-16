# agents/GovernanceAgent/Dockerfile
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app
# No special apt-get dependencies needed for this agent currently

COPY . /app

# Install Python dependencies from the root requirements.txt
# Ensure 'redis' is in requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Command to run the agent's worker
CMD ["python", "-u", "agents/GovernanceAgent/app/agent.py"]
