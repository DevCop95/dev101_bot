FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# Añade --upgrade para romper el caché
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY . .

CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 120 bot:app
