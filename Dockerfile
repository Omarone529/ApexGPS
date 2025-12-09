FROM python:3.10-slim
WORKDIR /app

RUN apt-get update && \
    apt-get install -y \
    libpq-dev \
    gcc \
    python3-dev \
    python3-gdal \
    gdal-bin \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 django && chown -R django:django /app
USER django
COPY --chown=django:django requirements.txt .

RUN grep -v "djangorestframework-gis" requirements.txt > requirements_no_gis.txt && \
    pip install --no-cache-dir --user -r requirements_no_gis.txt

RUN pip install --no-cache-dir --user djangorestframework-gis

ENV PATH="/home/django/.local/bin:${PATH}"
COPY --chown=django:django . .

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
