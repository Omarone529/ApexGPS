FROM python:3.10-slim

WORKDIR /app

COPY apt-requirements.sh /usr/local/bin/apt-requirements.sh
RUN chmod +x /usr/local/bin/apt-requirements.sh

RUN /usr/local/bin/apt-requirements.sh

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

ENV PATH="/root/.local/bin:${PATH}"
ENV PYTHONPATH="/app:${PYTHONPATH}"
ENV GDAL_DATA="/usr/share/gdal"

COPY . .

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
