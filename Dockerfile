FROM python:3.14-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

RUN groupadd --gid 10001 payments && useradd --uid 10001 --gid payments --no-create-home payments

COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY migrations ./migrations

USER payments

EXPOSE 8000

CMD ["uvicorn", "payments.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test

USER root
COPY requirements-dev.lock .
RUN pip install --no-cache-dir -r requirements-dev.lock
COPY tests ./tests
RUN chown -R payments:payments /app
USER payments

CMD ["pytest", "--cov", "--cov-report=term-missing"]

FROM runtime AS production
