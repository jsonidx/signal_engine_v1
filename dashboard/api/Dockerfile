FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY ../../requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir \
        anthropic \
        psycopg2-binary \
        python-jose[cryptography] \
        passlib

# Copy the full project (signal engine modules + dashboard api)
COPY ../../ /app/

# The API entrypoint is dashboard/api/main.py
WORKDIR /app/dashboard/api

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
