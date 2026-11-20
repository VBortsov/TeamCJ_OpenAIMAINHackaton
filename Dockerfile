FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
RUN useradd --system --create-home --uid 10001 founderhunt && mkdir data && chown founderhunt data
COPY backend backend
COPY first-signal first-signal
COPY --chown=founderhunt:founderhunt data data
USER founderhunt
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
