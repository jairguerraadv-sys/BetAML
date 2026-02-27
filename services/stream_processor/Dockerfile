FROM python:3.11-slim
WORKDIR /app
COPY services/stream_processor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/stream_processor/processor ./processor
COPY libs /libs
ENV PYTHONPATH=/app:/libs
CMD ["python", "-m", "processor.main"]
