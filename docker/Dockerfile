FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl wget gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20.x for Playwright CLI
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /app/.venv
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:/usr/local/bin:${PATH}"
RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --active --frozen

RUN playwright install chromium --with-deps

# Install Playwright CLI for AI agent browser automation
RUN npm install -g @playwright/cli@latest
RUN playwright-cli install

COPY . .
RUN mkdir -p sandbox

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
