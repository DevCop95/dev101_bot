FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Forma correcta: shell form para que $PORT se expanda
CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 120 bot:app
