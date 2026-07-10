FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY datahub_mcp.py .

# run as non-root
RUN useradd --system --uid 10001 --no-create-home mcp
USER mcp

ENV PORT=8000
EXPOSE 8000

# curl isn't in slim; probe /health with the interpreter we already have
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=4)"]

CMD ["python", "datahub_mcp.py", "--http"]
