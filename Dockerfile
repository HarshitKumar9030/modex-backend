FROM python:3.12-slim

# System deps for pdf2image (poppler) and pydub (ffmpeg)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        poppler-utils \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/storage/uploads /app/storage/outputs

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
