FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system appgroup \
    && useradd --system --gid appgroup --create-home appuser

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tests ./tests

USER appuser

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
