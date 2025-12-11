FROM python:3.10-slim
WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    libpq-dev \
    gdal-bin \
    libgdal-dev \
    python3-gdal \
    binutils \
    libproj-dev \
    build-essential \
    libssl-dev \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    libgeos-dev \
    libspatialindex-dev \
    curl \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 django && chown -R django:django /app
USER django
COPY --chown=django:django requirements.txt .

RUN pip install --no-cache-dir --user \
    numpy==1.26.4 \
    && pip install --no-cache-dir --user \
    pandas==2.1.4 \
    shapely==2.0.2 \
    fiona==1.9.5 \
    pyproj==3.6.1 \
    rasterio==1.3.9 \
    boto3==1.34.125 \
    geopandas==0.14.3 \
    && pip install --no-cache-dir --user -r requirements.txt

ENV PATH="/home/django/.local/bin:${PATH}"
ENV PYTHONPATH="/app:${PYTHONPATH}"
ENV GDAL_DATA="/usr/share/gdal"
COPY --chown=django:django . .
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
