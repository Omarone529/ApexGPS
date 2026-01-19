FROM python:3.10-slim

COPY apt-requirements.sh /usr/local/bin/apt-requirements.sh
RUN chmod +x /usr/local/bin/install-deps.sh

RUN /usr/local/bin/install-deps.sh

WORKDIR /app

COPY requirements.txt .
COPY requirements-dev.txt .

RUN pip install --no-cache-dir --user -r requirements.txt

ENV PATH="/home/django/.local/bin:${PATH}"
ENV PYTHONPATH="/app:${PYTHONPATH}"
ENV GDAL_DATA="/usr/share/gdal"
COPY . .
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
