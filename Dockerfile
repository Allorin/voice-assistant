FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

WORKDIR /app
COPY requirements.txt requirements-postgres.txt requirements-docker.txt ./
# psycopg — для Postgres через DATABASE_URL; huggingface_hub — резервная копия SQLite в датасет (BACKUP_REPO)
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY app ./app
COPY certs ./certs
COPY data/events_seed.csv ./data/events_seed.csv

# Hugging Face Spaces запускает контейнер не от root — даём права на папку с базой
RUN mkdir -p /app/data && chmod -R 777 /app/data

EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
