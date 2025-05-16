# =================================
# 📁 agents/TestAgent/Dockerfile
# =================================
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app

# No special apt-get dependencies anticipated for this agent's V0.1
# If it were to run actual test frameworks that need system libs, add them here.

# Copy the entire repository context into /app
COPY . /app

# Install Python dependencies from the root requirements.txt
# Ensure 'redis' (for EventBus) is in requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Command to run the agent's main script (its worker loop)
CMD ["python", "-u", "agents/TestAgent/app/agent.py"]
