FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

WORKDIR /app
COPY requirements.txt requirements-postgres.txt ./
# psycopg — для внешнего Postgres через DATABASE_URL; без него база — SQLite (на Amvera — на постоянном диске /data)
RUN pip install --no-cache-dir -r requirements-postgres.txt

COPY app ./app
COPY certs ./certs
COPY data/events_seed.csv ./data/events_seed.csv

# папка для локальной базы; хостинг может запускать контейнер не от root
RUN mkdir -p /app/data && chmod -R 777 /app/data

EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
