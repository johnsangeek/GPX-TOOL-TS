#!/bin/bash
# GPX Overlay — Stoppe tout

pkill -f "uvicorn main:app" 2>/dev/null && echo "🛑 Backend stoppé" || echo "Backend déjà arrêté"
pkill -f "expo start" 2>/dev/null && echo "🛑 Expo stoppé" || echo "Expo déjà arrêté"
