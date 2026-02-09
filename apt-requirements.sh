#!/usr/bin/env bash

set -e  # Exit immediately if a command exits with non-zero status

echo "Updating package list and installing dependencies..."

apt-get update && apt-get install -y \
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
    unzip

echo "Cleaning up package lists..."
rm -rf /var/lib/apt/lists/*

echo "Dependencies installed successfully!"
