FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY . .

# --- Security hardening ---
# Create a dedicated non-root user for running the application.
# The application must never run as root inside the container.
# UID/GID 10001 is chosen to be outside the standard system range.
RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appgroup /app

# Switch to non-root user before starting the application
USER appuser

# Run Alembic migrations then start Uvicorn
# NOTE: alembic upgrade requires write access to the migrations dir which is
# owned by appuser above. If migration write access becomes an issue in a
# stricter read-only filesystem configuration, split migrations into a
# separate init-container or entrypoint script run before USER switch.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]
