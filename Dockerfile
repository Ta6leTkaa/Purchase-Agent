FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN addgroup --system purchase-agent \
    && adduser --system --ingroup purchase-agent purchase-agent

COPY pyproject.toml README.md alembic.ini ./
RUN python -m pip install "playwright>=1.55.0" \
    && python -m playwright install --with-deps chromium \
    && chown -R purchase-agent:purchase-agent /ms-playwright

COPY app ./app
COPY alembic ./alembic

RUN python -m pip install .

USER purchase-agent

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
