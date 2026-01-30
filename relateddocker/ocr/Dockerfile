FROM python:3.10-slim

WORKDIR /app

# 常用依赖（opencv）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libxml2 \
    libxslt1.1 \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY ocr_service.py /app/ocr_service.py

EXPOSE 8000
CMD ["uvicorn", "ocr_service:app", "--host", "0.0.0.0", "--port", "8000"]
