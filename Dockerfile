FROM python:3.10-slim
WORKDIR /app
# Installing system dependencies (GDAL, PostGIS libs, etc.)
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    libgdal-dev \
    libproj-dev \
    # cache cleaning
    && rm -rf /var/lib/apt/lists/*

# install requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
EXPOSE 8000