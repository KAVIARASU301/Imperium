#!/bin/bash

PROJECT_DIR="/home/kavi/Desktop/Python_Projects/Imperium"
VENV_DIR="/home/kavi/Desktop/Python_Projects/.venv"

cd "$PROJECT_DIR" || exit 1

# Activate virtual environment
source "$VENV_DIR/bin/activate"

# Debug logging
echo "Launching Imperium at $(date)" >> "$PROJECT_DIR/launcher.log"

exec "$VENV_DIR/bin/python" main.py >> "$PROJECT_DIR/launcher.log" 2>&1
