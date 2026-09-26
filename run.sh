#!/usr/bin/env bash
# Ephemeris -> http://localhost:5055
cd "$(dirname "$0")"
exec ../venv/bin/python app.py
