FROM python:3.11.9-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Internal service only: uvicorn binds 0.0.0.0 inside the container network;
# no host port is published (Section 25 network isolation).
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
