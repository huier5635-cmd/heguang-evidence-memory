FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock && useradd -m -u 10001 memory
COPY aml_memory /app/aml_memory
RUN mkdir /app/data && chown -R memory:memory /app
USER memory
ENV AML_DATA_DIR=/app/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["uvicorn", "aml_memory.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]

