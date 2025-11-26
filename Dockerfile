FROM python:3.10-slim
WORKDIR /app
# Installing system dependencies for GeoDjango
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    libgdal-dev \
    libproj-dev \
    binutils \
    # cache cleaning \
    && rm -rf /var/lib/apt/lists/*

# install requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
EXPOSE 8000
