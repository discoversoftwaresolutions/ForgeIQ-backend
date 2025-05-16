# ===================================
# 📁 agents/PatchAgent/Dockerfile
# ===================================
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app

# No special apt-get dependencies anticipated for this agent's V0.1
# (httpx, its main external call lib, is pure Python)

# Copy the entire repository context into /app
COPY . /app

# Install Python dependencies from the root requirements.txt
# Ensure 'redis' (for EventBus) and 'httpx' are in requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Command to run the agent's main script (its event listening loop)
CMD ["python", "-u", "agents/PatchAgent/app/agent.py"]
