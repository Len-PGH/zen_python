#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# Define the virtual environment directory name
VENV_DIR="venv"

# Check if the virtual environment exists
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python -m venv "$VENV_DIR"
fi

# Activate the virtual environment
echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Install dependencies
if [ -f "requirements.txt" ]; then
    echo "Installing dependencies from requirements.txt..."
    pip install -r requirements.txt
else
    echo "requirements.txt not found. Skipping dependency installation."
fi

# Navigate to the application directory (if necessary - assuming script is at project root)
# cd server/Python_Examples/zen_python

# Run the main application script
echo "Starting the application..."
python server/Python_Examples/zen_python/app.py

# Deactivate the virtual environment when the script finishes (optional, depends on how you run it)
# deactivate 