FROM python:3.12-slim

# System deps for pdf2image (poppler), pydub (ffmpeg), and OCR (tesseract)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        poppler-utils \
        ffmpeg \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Explicit path for pdf2image poppler binaries inside Debian slim image
ENV POPPLER_PATH=/usr/bin

# Fail build early if poppler binaries are unavailable
RUN pdftoppm -v >/dev/null

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/storage/uploads /app/storage/outputs

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
