# Build context is the repo root:  docker build -f services/mcp-server/Dockerfile .
FROM python:3.12-slim
WORKDIR /app
COPY libs/common /libs/common
COPY services/mcp-server/pyproject.toml .
RUN pip install --no-cache-dir /libs/common .
COPY services/mcp-server/app ./app
RUN useradd -r -u 10003 appuser
USER appuser
EXPOSE 8003
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
