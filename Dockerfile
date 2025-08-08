FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Создаём папку для базы, если не существует
RUN mkdir -p /app/data

CMD ["python", "bot.py"]
