#!/bin/bash

# Script to activate venv, install dependencies, check .env, and run the Flask app.

VENV_DIR="venv"
REQUIREMENTS="requirements.txt"
ENV_FILE=".env"

# --- 1. Check for Virtual Environment ---
if [ ! -d "$VENV_DIR" ]; then
  echo "Error: Virtual environment '$VENV_DIR' not found."
  echo "Please run './setup_venv.sh' first to create it."
  exit 1
fi

# --- 2. Activate Virtual Environment ---
echo "Activating virtual environment..."
source "$VENV_DIR/bin/activate"
if [ $? -ne 0 ]; then
  echo "Error: Failed to activate virtual environment."
  exit 1
fi

# --- 3. Install/Update Dependencies ---
echo "Installing/Updating dependencies from $REQUIREMENTS..."
pip install -r "$REQUIREMENTS"
if [ $? -ne 0 ]; then
  echo "Error: Failed to install dependencies."
  # Deactivate venv before exiting on error
  deactivate
  exit 1
fi

# --- 4. Check for .env file ---
if [ ! -f "$ENV_FILE" ]; then
  echo "Error: Configuration file '$ENV_FILE' not found."
  echo "Please create it and add your GOOGLE_API_KEY."
  deactivate
  exit 1
fi

# --- 5. Basic Check for GOOGLE_API_KEY in .env ---
# This is a simple check, it doesn't validate the key itself
if ! grep -q "^GOOGLE_API_KEY=.*'.*'" "$ENV_FILE" && ! grep -q '^GOOGLE_API_KEY=.*".*"' "$ENV_FILE" && ! grep -q '^GOOGLE_API_KEY=[^#].*' "$ENV_FILE"; then
  echo "Error: GOOGLE_API_KEY seems to be missing or commented out in '$ENV_FILE'."
  echo "Please ensure it is set correctly (e.g., GOOGLE_API_KEY='your_key_here')."
  deactivate
  exit 1
fi
# Check if the placeholder value is still there
if grep -q "GOOGLE_API_KEY='YOUR_API_KEY_HERE'" "$ENV_FILE"; then
  echo "Warning: Placeholder GOOGLE_API_KEY='YOUR_API_KEY_HERE' found in '$ENV_FILE'."
  echo "Please replace it with your actual API key before running."
  deactivate
  exit 1
fi


# --- 6. Run Flask Application ---
echo "Starting Flask application..."
# The .env file should be loaded automatically by the app thanks to python-dotenv
flask run --host=0.0.0.0 --port=8081

# Deactivate venv when Flask stops (e.g., Ctrl+C)
echo "Flask application stopped. Deactivating virtual environment."
deactivate

exit 0
