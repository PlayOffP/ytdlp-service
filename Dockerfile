# Use Python 3.11 with FFmpeg support
FROM python:3.11-slim

# Install FFmpeg and other dependencies optimized for Railway
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create temp directory for audio processing
RUN mkdir -p /tmp/audio-processing

# Set environment variables for Railway optimization
ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=production
ENV GUNICORN_TIMEOUT=600
ENV GUNICORN_WORKERS=2
ENV GUNICORN_WORKER_CLASS=sync
ENV GUNICORN_MAX_REQUESTS=100
ENV GUNICORN_MAX_REQUESTS_JITTER=10

# Expose port
EXPOSE 5000

# Start the application with optimized Gunicorn settings for long-running requests
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "600", "--workers", "2", "--worker-class", "sync", "--max-requests", "100", "--max-requests-jitter", "10", "app:app"]