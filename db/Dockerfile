FROM postgis/postgis:14-3.4

# Install pgRouting
RUN apt-get update \
    && apt-get install -y postgresql-14-pgrouting \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /docker-entrypoint-initdb.d

EXPOSE 5432
