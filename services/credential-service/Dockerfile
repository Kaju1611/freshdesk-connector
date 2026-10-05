# Build context is the repo root:  docker build -f services/credential-service/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/common /libs/common
COPY services/credential-service/pyproject.toml .
RUN pip install --no-cache-dir /libs/common . 
COPY services/credential-service/app ./app
RUN useradd -r -u 10001 appuser && mkdir /data && chown appuser /data
USER appuser
EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
