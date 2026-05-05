#!/bin/bash
gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --timeout 120 dashboard:app &
python cinax_v02.py
 
