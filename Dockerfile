FROM python:3.10-slim
WORKDIR /app

RUN apt-get update && apt-get install -y \
    libpq-dev gcc libgdal-dev gdal-bin libproj-dev binutils \
    && rm -rf /var/lib/apt/lists/*

# Cerca il percorso corretto di libgdal.so
RUN find /usr -name "libgdal.so*" 2>/dev/null

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
EXPOSE 8000