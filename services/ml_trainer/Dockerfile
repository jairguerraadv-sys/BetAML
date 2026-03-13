FROM python:3.11-slim

WORKDIR /app

COPY services/ml_trainer/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copia o script do trainer e o módulo de API (config + models)
COPY services/ml_trainer/main.py ./main.py
COPY services/api/config.py ./config.py
COPY services/api/models.py ./models.py
COPY services/api/database.py ./database.py
COPY services/api/auth.py ./auth.py

CMD ["python", "main.py"]
