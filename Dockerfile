# ========================================
# 📁 apps/forgeiq-backend/Dockerfile
# For the Python/FastAPI ForgeIQ Backend
# ========================================

# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container to /app
# All subsequent commands and file paths will be relative to this
WORKDIR /app

# Set environment variables
# Ensures Python output is sent straight to terminal without being buffered first
ENV PYTHONUNBUFFERED=1
# Adds the /app directory (monorepo root inside container) to PYTHONPATH
# so Python can find your core, interfaces, shared, and apps packages
ENV PYTHONPATH=/app

# Install system dependencies that might be needed by your Python packages
# Add any other 'apt-get install -y ...' commands here if required
# For a basic FastAPI app, often none are needed beyond what python:slim provides.
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     # Example: build-essential libpq-dev (if using psycopg2 with C extensions) \
#     && rm -rf /var/lib/apt/lists/*

# Copy the root requirements.txt first to leverage Docker layer caching
# This assumes all Python dependencies for all services in the monorepo
# (including fastapi, uvicorn, pydantic, redis, httpx for this backend)
# are listed in the root requirements.txt file.
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of your monorepo's code into the /app directory in the image.
# This includes the 'apps/forgeiq-backend/app/main.py' and any 'core',
# 'interfaces', 'shared' Python modules it depends on.
# Using "COPY . ." is simple but copies everything. For production, you might
# selectively COPY only necessary directories (e.g., COPY apps /app/apps, COPY core /app/core)
# However, with Railway's Watch Paths, `COPY . .` is often manageable.
COPY . .

# The port your FastAPI application (run by Uvicorn) will listen on *inside* the container.
# Railway will automatically assign a public $PORT and map it to this internal port.
# So, Uvicorn should listen on this internal port.
EXPOSE 8000

# Command to run your FastAPI application using Uvicorn.
# This tells Uvicorn to:
#   - Find the 'app' instance (your FastAPI app) in the Python module 'apps.forgeiq-backend.app.main'.
#   - Listen on all available network interfaces within the container (0.0.0.0).
#   - Listen on port 8000 inside the container.
CMD ["uvicorn", "apps.forgeiq-backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
