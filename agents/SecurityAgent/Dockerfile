# agents/SecurityAgent/Dockerfile
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app

# Install git if your agent needs to clone repos or interact with git cli.
# Install other build-time dependencies for security tools if they are compiled.
# Example: For Bandit and Safety, pip install is usually enough.
# For Trivy (if run inside this container), you'd need to install Trivy binary.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    # Add other system dependencies for security tools here if needed
    && rm -rf /var/lib/apt/lists/*

COPY . /app

# Install Python dependencies from the root requirements.txt
# Ensure 'redis' and Python wrappers for any CLI security tools like 'bandit', 'safety'
# are in requirements.txt if you want to pip install them.
# Otherwise, this agent might assume tools are already in the PATH or in another container.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Command to run the agent's worker
CMD ["python", "-u", "agents/SecurityAgent/app/agent.py"]
