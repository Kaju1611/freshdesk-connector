# Build context is the repo root:  docker build -f services/freshdesk-adapter/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/common /libs/common
COPY services/freshdesk-adapter/pyproject.toml .
RUN pip install --no-cache-dir /libs/common .
COPY services/freshdesk-adapter/app ./app
RUN useradd -r -u 10002 appuser
USER appuser
EXPOSE 8002
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
