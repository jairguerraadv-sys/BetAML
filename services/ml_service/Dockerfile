FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
# Build context: raiz do monorepo (context: ../.. no compose)
COPY services/ml_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY libs /app/libs
RUN pip install --no-cache-dir /app/libs
COPY services/ml_service /app
ENV PYTHONPATH=/app
EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
