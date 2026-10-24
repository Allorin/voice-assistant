"""Запуск: python -m app  (PORT — порт, по умолчанию 8000; HOST — адрес, по умолчанию 127.0.0.1)."""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    if os.getenv("OPEN_BROWSER"):  # start.bat: страница откроется после успешного старта (см. app/main.py)
        os.environ["OPEN_BROWSER_URL"] = f"http://localhost:{port}"
    uvicorn.run("app.main:app", host=os.getenv("HOST", "127.0.0.1"), port=port)
