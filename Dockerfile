FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
# default: paper trading. docker-compose overrides this with "webhook".
CMD ["python", "run.py", "paper"]
