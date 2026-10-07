FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Jakarta

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY config ./config
COPY prompts ./prompts
COPY deploy ./deploy


RUN useradd --create-home --uid 1000 companion \
    && mkdir -p /app/data \
    && chown -R companion:companion /app/data
USER companion

CMD ["python", "-m", "app.main"]
