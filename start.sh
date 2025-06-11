#!/bin/bash

# Create necessary directories
mkdir -p static/uploads

# Initialize database if needed
python -c "
import os
import sys
sys.path.insert(0, os.getcwd())
from app import ensure_user_columns
ensure_user_columns()
print('Database initialized successfully')
"

# Start the application with Gunicorn
exec gunicorn --config gunicorn.conf.py app:app 