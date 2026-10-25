# CRIC watch loop as a container: Nebius Serverless AI Job (CPU) or any container runtime.
#   docker build -t cric .
#   docker run --env-file .env -v $PWD/data:/data cric watch --every 30 --cycles 1
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY fixtures ./fixtures
RUN pip install --no-cache-dir . && useradd -m cric
ENV CRIC_DATA_DIR=/data \
    CRIC_FLEET=/app/fixtures/fleet.json \
    CRIC_SNAPSHOT=/app/fixtures/snapshot_20261025
RUN mkdir -p /data && chown cric /data
USER cric
ENTRYPOINT ["cric"]
CMD ["watch", "--every", "30"]
