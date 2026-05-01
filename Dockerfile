FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Usa gunicorn como servidor de producción
CMD ["gunicorn", "--bind", "0.0.0.0:$PORT", "--workers", "1", "--timeout", "120", "bot:app"]
