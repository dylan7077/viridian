FROM python:3.11-slim

# OpenCV + tesseract system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/     src/
COPY web/     web/
COPY config.py .

# data/ is a persistent volume mounted at runtime — never baked into the image.
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1

# Railway injects $PORT; default to 8000 for local runs.
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
