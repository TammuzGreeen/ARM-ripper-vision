FROM python:3.12.10-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/state
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-deu libzbar0 ffmpeg v4l-utils \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 568 queue && useradd --uid 568 --gid 568 --no-create-home queue
WORKDIR /app
COPY arm-season-queue/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY arm-season-queue/app ./app
COPY arm-season-queue/static ./static
COPY arm-season-queue/scripts ./scripts
COPY arm-season-queue/examples ./examples
USER 568:568
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3)"
CMD ["python", "-m", "app.main"]
