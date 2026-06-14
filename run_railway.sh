#!/bin/bash
# Enable bash exit on error
set -e

# Start the Telegram bot daemon in the background
echo "🚀 Starting Telegram Bot..."
python bot.py &

# Start the Streamlit admin dashboard in the foreground
echo "📊 Starting Streamlit Dashboard on port ${PORT:-8501}..."
streamlit run streamlit_app.py --server.port ${PORT:-8501} --server.address 0.0.0.0
