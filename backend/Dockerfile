FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
ARG BUILD_COMMIT=local
ENV BUILD_COMMIT=$BUILD_COMMIT
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 liftapi && useradd --uid 10001 --gid 10001 --no-create-home liftapi
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./
RUN DJANGO_DEBUG=True python manage.py collectstatic --noinput
RUN mkdir -p /app/private_media && chmod 700 /app/private_media && chown -R liftapi:liftapi /app
USER 10001:10001
EXPOSE 8000
CMD ["gunicorn", "liftcontrol.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "2", "--timeout", "45", "--access-logfile", "-", "--error-logfile", "-"]

