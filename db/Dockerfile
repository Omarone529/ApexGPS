FROM postgis/postgis:14-3.4

# pgRouting package
RUN apt-get update \
    && apt-get install -y postgresql-14-pgrouting \
    && rm -rf /var/lib/apt/lists/*
