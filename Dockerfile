FROM python:3.11-slim

WORKDIR /app

# system deps for pillow/compilation if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ensure bot reads BOT_TOKEN and ADMIN_CHAT_ID from env
ENV DISABLE_DASHBOARD=1

CMD ["python", "bot.py"]
