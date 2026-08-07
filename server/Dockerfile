FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY agentlens_server ./agentlens_server
RUN pip install --no-cache-dir .
EXPOSE 7430
CMD ["uvicorn", "agentlens_server.main:app", "--host", "0.0.0.0", "--port", "7430"]
