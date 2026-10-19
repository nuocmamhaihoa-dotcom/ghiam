FROM python:3.12-slim

RUN useradd --create-home --uid 10001 coder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY docker/entrypoint.py /entrypoint.py
EXPOSE 8080
ENTRYPOINT ["python", "/entrypoint.py"]
