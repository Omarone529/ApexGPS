FROM python:3.10-slim
WORKDIR /app

RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    libgdal-dev \
    gdal-bin \
    libgeos-dev \
    libproj-dev \
    binutils \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 django && chown -R django:django /app
USER django
COPY --chown=django:django requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt
ENV PATH="/home/django/.local/bin:${PATH}"
COPY --chown=django:django . .

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]