# Сертификаты для GigaChat

API GigaChat работает на сертификате Национального удостоверяющего центра (НУЦ Минцифры).
Python по умолчанию ему не доверяет, поэтому корневой сертификат лежит здесь, и приложение
подхватывает его автоматически (`app/config.py`, `resolve_ca_bundle`).

- `russian_trusted_root_ca.pem` — Russian Trusted Root CA, публичный корневой сертификат.
  Источник: https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
  (ссылка из документации GigaChat: https://developers.sber.ru/docs/ru/gigachat/certificates).

Проверка: `openssl x509 -in certs/russian_trusted_root_ca.pem -noout -subject -enddate -fingerprint -sha256`
должна показать `CN = Russian Trusted Root CA`, срок до 27.02.2032 и отпечаток SHA-256
`D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`.

Это открытый сертификат, а не секрет. Ключ GigaChat хранится только в `.env` и в секретах хостинга.
