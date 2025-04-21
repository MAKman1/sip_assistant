#!/bin/bash

# Script to create the Python virtual environment ('venv') if it doesn't exist.

VENV_DIR="venv"

# Check if the venv directory already exists
if [ -d "$VENV_DIR" ]; then
  echo "Virtual environment '$VENV_DIR' already exists."
else
  echo "Creating virtual environment '$VENV_DIR'..."
  # Use python3 explicitly for better compatibility on macOS/Linux
  python3 -m venv "$VENV_DIR"
  if [ $? -eq 0 ]; then
    echo "Virtual environment created successfully."
    echo "Activate it using: source $VENV_DIR/bin/activate"
  else
    echo "Error: Failed to create virtual environment."
    exit 1
  fi
fi

exit 0
