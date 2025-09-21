#!/bin/bash

echo "🚀 Starting Telegram Protection Bot..."

# Install Python dependencies
echo "🐍 Upgrading pip and installing Python dependencies..."
pip3 install --upgrade pip
pip3 install -r requirements.txt --no-cache-dir

# Start the Python bot
echo "🌟 Starting Python bot..."
python3 main.py


