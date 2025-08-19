# Use official Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Set environment variables from .env (optional, for local dev)
# ENV DECODO_API_KEY=your_key
# ENV TELEGRAM_BOT_TOKEN=your_token

# Run the bot
CMD ["python", "bot.py"]
