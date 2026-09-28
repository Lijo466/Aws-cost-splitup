# Use official lightweight Python image with pinned version
FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create a non-root user and group
RUN groupadd --gid 1001 appgroup && \
    useradd --uid 1001 --gid appgroup --no-create-home appuser

# Set working directory
WORKDIR /app

# Install dependencies as root (before dropping privileges)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py db.py ec2_scanner.py s3_scanner.py ./
COPY templates/ templates/

# Set ownership to non-root user
RUN chown -R appuser:appgroup /app

# Drop to non-root user
USER appuser

# Expose Flask port
EXPOSE 5000

# Run with gunicorn (production WSGI server — no debug mode, no reloader)
# Worker/thread/timeout values are injected via environment variables from .env
CMD gunicorn \
    --bind 0.0.0.0:${FLASK_PORT:-5000} \
    --workers ${GUNICORN_WORKERS:-2} \
    --threads ${GUNICORN_THREADS:-4} \
    --timeout ${GUNICORN_TIMEOUT:-120} \
    app:app
