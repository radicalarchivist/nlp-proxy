FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download spaCy model
RUN python -m spacy download en_core_web_sm

COPY app.py .

# Healthcheck for Docker
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -fs http://localhost:5000/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]
