#!/bin/bash

# US Lobbying Equities Strategy Launcher
echo "=================================================="
echo "US Lobbying Equities Long Only Strategy"
echo "Institutional-Grade Lobbying Analytics Tool"
echo "=================================================="
echo ""

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install/update dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt

# Create data directory if it doesn't exist
mkdir -p data

echo ""
echo "Starting application..."
echo "Opening browser at http://localhost:8501"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Run the application
streamlit run app.py