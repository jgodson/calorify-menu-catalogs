FROM python:3.13-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    PORT=8080

# pdftotext reads uploaded nutrition PDFs (Subway)
RUN apk add --no-cache poppler-utils

WORKDIR /app
COPY menu_catalogs ./menu_catalogs

RUN adduser -D -u 10001 app && mkdir -p /data && chown 10001:10001 /data
USER 10001

EXPOSE 8080
VOLUME ["/data"]
CMD ["python", "-m", "menu_catalogs", "serve"]
