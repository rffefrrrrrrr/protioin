#!/bin/bash

echo "🚀 Starting Telegram Protection Bot Server..."

# Install Node.js dependencies
echo "📦 Installing Node.js dependencies..."
npm install

# Install Python dependencies
echo "🐍 Upgrading pip and installing Python dependencies..."
pip3 install --upgrade pip
pip3 install -r requirements.txt --no-cache-dir

# Start the server
echo "🌟 Starting Express server..."
npm start

