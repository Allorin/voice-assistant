FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

WORKDIR /app
COPY requirements.txt requirements-postgres.txt requirements-voice.txt ./
# psycopg — для внешнего Postgres через DATABASE_URL; без него база — SQLite (на Amvera — на постоянном диске /data)
# vosk — офлайн-распознавание речи для браузеров без своего (Firefox, Opera, Яндекс Браузер)
RUN pip install --no-cache-dir -r requirements-postgres.txt -r requirements-voice.txt

# модель распознавания речи (~45 МБ) и голос для озвучки (~63 МБ); если что-то не скачалось,
# эту часть голоса берёт на себя браузер (Chrome, Edge, Safari)
COPY scripts/download_vosk_model.py scripts/download_piper_voice.py ./scripts/
RUN python scripts/download_vosk_model.py \
    || echo "Модель Vosk не скачалась: голосовой ввод будет работать только в Chrome, Edge и Safari"
RUN python scripts/download_piper_voice.py \
    || echo "Голос Piper не скачался: озвучка будет работать только в браузерах с русским голосом"

COPY app ./app
COPY certs ./certs
COPY data/events_seed.csv ./data/events_seed.csv

# папка для локальной базы; хостинг может запускать контейнер не от root
RUN mkdir -p /app/data && chmod -R 777 /app/data

EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
