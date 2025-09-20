#!/bin/bash

echo "🚀 Starting Telegram Protection Bot Server..."

# Install Node.js dependencies
echo "📦 Installing Node.js dependencies..."
npm install

# Install Python dependencies
echo "🐍 Installing Python dependencies..."
pip3 install -r requirements.txt

# Start the server
echo "🌟 Starting Express server..."
npm start

