FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY backend/requirements.txt backend/requirements.txt
COPY bot/requirements.txt bot/requirements.txt
RUN pip install -r backend/requirements.txt -r bot/requirements.txt

COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
