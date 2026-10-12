FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN DJANGO_SECRET_KEY=build-only-not-used python manage.py collectstatic --noinput \
    && useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
# The Host header must be allowed: keep "localhost" in DJANGO_ALLOWED_HOSTS.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/api/health/', timeout=3)"
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2"]
