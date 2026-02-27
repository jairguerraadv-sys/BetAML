FROM python:3.11-slim
WORKDIR /app
COPY services/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/api/app ./app
COPY services/api/alembic ./alembic
COPY services/api/alembic.ini .
COPY libs /libs
ENV PYTHONPATH=/app:/libs
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
