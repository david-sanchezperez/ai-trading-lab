FROM python:3.12-slim

# ping necesario para healthcheck del PC
RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY docker/ds220/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Solo copiamos lo que necesita este servicio
COPY scheduler/ ./scheduler/

CMD ["python", "-m", "scheduler.ds220_scheduler"]
