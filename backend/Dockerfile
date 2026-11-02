FROM python:3.12-slim

# Tools the agent will want: git (workspaces), ripgrep (fast grep), node/npm, build tools, archive utils.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git ripgrep nodejs npm curl ca-certificates unzip zip jq build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Real Chromium for the agent's browser tool (installed system-wide so the unprivileged user can run it).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium && chmod -R a+rX /ms-playwright && rm -rf /var/lib/apt/lists/*
COPY app ./app

# Run as a normal user: the agent's shell can't touch the system.
RUN useradd -m -u 1000 agent && mkdir -p /tmp/agent && chown -R agent /app /tmp/agent
USER agent
ENV PORT=10000 DATA_DIR=/tmp/agent PYTHONUNBUFFERED=1
EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
