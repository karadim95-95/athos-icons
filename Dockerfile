FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN DEBUG=0 SECRET_KEY=build-only python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py setup_shop && gunicorn config.wsgi -b 0.0.0.0:8000 -w 3 --timeout 120 --access-logfile -"]
