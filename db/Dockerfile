FROM postgis/postgis:18-3.6

# Install pgRouting
RUN apt-get update && apt-get install -y \
    postgis \
    htop \
    postgresql-18-postgis-3 \
    postgresql-18-pgrouting \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /docker-entrypoint-initdb.d

EXPOSE 5432
