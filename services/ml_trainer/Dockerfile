FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o script do trainer e o módulo de API (config + models)
COPY main.py .
COPY ../api/config.py ./config.py
COPY ../api/models.py ./models.py
COPY ../api/database.py ./database.py
COPY ../api/auth.py ./auth.py

CMD ["python", "main.py"]
