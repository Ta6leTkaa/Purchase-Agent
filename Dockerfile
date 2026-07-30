FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system purchase-agent \
    && adduser --system --ingroup purchase-agent purchase-agent

COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
COPY alembic ./alembic

RUN python -m pip install .

USER purchase-agent

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
