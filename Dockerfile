FROM python:3.11-slim

WORKDIR /app

# Install uv
RUN pip install uv

# Copy dependency files first (layer cache)
COPY pyproject.toml .

# Install dependencies
RUN uv pip install --system -e ".[dev]"

# Copy source
COPY . .

EXPOSE 8000

CMD ["uvicorn", "qutlas.platform_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
