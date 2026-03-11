FROM python:3.10-slim

WORKDIR /app

# Install system dependencies (your apt-requirements.sh)
COPY apt-requirements.sh /usr/local/bin/apt-requirements.sh
RUN sed -i 's/\r$//' /usr/local/bin/apt-requirements.sh && \
    chmod +x /usr/local/bin/apt-requirements.sh && \
    /usr/local/bin/apt-requirements.sh

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Environment variables for GDAL etc.
ENV PATH="/root/.local/bin:${PATH}" \
    PYTHONPATH="/app:${PYTHONPATH}" \
    GDAL_DATA="/usr/share/gdal"

# Copy application code
COPY . .

# Collect static files (they will be included in the image)
RUN python manage.py collectstatic --noinput

# Expose the port Gunicorn will listen on
EXPOSE 8000

# Use Gunicorn as the production server
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "apexgps.wsgi:application"]