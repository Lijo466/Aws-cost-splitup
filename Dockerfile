# Use official lightweight Python image
FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AWS_SHARED_CREDENTIALS_FILE=/home/appuser/.aws/credentials

# Install fail2ban, iptables, and security utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    fail2ban \
    iptables \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy fail2ban configurations for Port 5000 security monitoring
COPY fail2ban/gunicorn-filter.conf /etc/fail2ban/filter.d/gunicorn-filter.conf
COPY fail2ban/gunicorn-jail.conf /etc/fail2ban/jail.d/gunicorn-jail.conf

# Copy all application Python modules (including cost_explorer.py & project_detector.py)
COPY *.py ./

# Copy static assets (sync_reconciler.js & session_timeout.js) and HTML templates
COPY static/ static/
COPY templates/ templates/

# Create log & data directories
RUN mkdir -p data /var/log/gunicorn

# Copy entrypoint script and grant execution permissions
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Expose Flask application port
EXPOSE 5000

# Health check to ensure API is responding on Port 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health')"

# Use entrypoint script to launch Fail2ban and Gunicorn
ENTRYPOINT ["/entrypoint.sh"]
