FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY ../../libs /libs
ENV PYTHONPATH=/app:/libs
CMD ["python", "-m", "engine.main"]
