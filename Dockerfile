FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY coeos_se ./coeos_se
RUN pip install --no-cache-dir .

# Config (holds your keys once saved via the dashboard) lives on the volume.
ENV COEOS_CONFIG=/data/coeos-config.json \
    COEOS_PORT=4600
VOLUME /data
EXPOSE 4600

CMD ["coeos-se"]
