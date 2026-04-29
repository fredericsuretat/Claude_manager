FROM python:3.11-slim

WORKDIR /app

# Install claude CLI dependencies (node, npm)
RUN apt-get update && apt-get install -y \
    curl procps \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js for claude CLI
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install claude CLI globally
RUN npm install -g @anthropic-ai/claude-code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8765

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--reload"]
