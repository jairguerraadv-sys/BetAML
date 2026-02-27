FROM python:3.11-slim
WORKDIR /app
COPY services/ml_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/ml_service/ml ./ml
COPY libs /libs
ENV PYTHONPATH=/app:/libs
CMD ["uvicorn", "ml.main:app", "--host", "0.0.0.0", "--port", "8001"]
