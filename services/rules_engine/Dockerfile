FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
# Build context: raiz do monorepo (context: ../.. no compose)
COPY services/rules_engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY libs /app/libs
RUN pip install --no-cache-dir /app/libs
COPY services/rules_engine /app
ENV PYTHONPATH=/app
CMD ["python", "main.py"]
