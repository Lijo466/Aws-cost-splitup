#!/bin/sh
set -e

# Auto-detect AWS credentials file location
if [ -f "/home/appuser/.aws/credentials" ]; then
    export AWS_SHARED_CREDENTIALS_FILE="/home/appuser/.aws/credentials"
    echo "Using AWS credentials from /home/appuser/.aws/credentials"
elif [ -f "/root/.aws/credentials" ]; then
    export AWS_SHARED_CREDENTIALS_FILE="/root/.aws/credentials"
    echo "Using AWS credentials from /root/.aws/credentials"
fi

# Create log & data directories
mkdir -p /var/log/gunicorn /app/data
touch /var/log/gunicorn/access.log /var/log/gunicorn/error.log

# Start Fail2ban security monitoring daemon on Port 5000 if available
if command -v fail2ban-server >/dev/null 2>&1; then
    echo "Starting Fail2ban security monitoring daemon on Port 5000..."
    service fail2ban start 2>/dev/null || fail2ban-client start 2>/dev/null || true
fi

# Execute Gunicorn production WSGI server
echo "Starting Gunicorn production server on Port 5000..."
exec gunicorn --bind 0.0.0.0:${FLASK_PORT:-5000} \
    --workers ${GUNICORN_WORKERS:-2} \
    --threads ${GUNICORN_THREADS:-4} \
    --timeout ${GUNICORN_TIMEOUT:-120} \
    --access-logfile /var/log/gunicorn/access.log \
    --error-logfile /var/log/gunicorn/error.log \
    --access-logformat '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"' \
    app:app
