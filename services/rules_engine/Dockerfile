FROM python:3.11-slim
WORKDIR /app
COPY services/rules_engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/rules_engine/engine ./engine
COPY libs /libs
ENV PYTHONPATH=/app:/libs
CMD ["python", "-m", "engine.main"]
