"""
GPX Overlay Backend — FastAPI Server
=====================================
Démarrage : uvicorn main:app --reload --port 8000

Variables d'environnement :
  GPX_WORK_DIR          : dossier de travail (défaut: /tmp/gpx_overlay_sessions)
  STRAVA_CLIENT_ID      : Client ID de ton app Strava
  STRAVA_CLIENT_SECRET  : Client Secret de ton app Strava
  STRAVA_REDIRECT_URI   : deep link iOS (défaut: gpxoverlay://strava-callback)
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# Charge le .env local si présent
_env_file = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_file):
    for _line in open(_env_file):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from api.routes import router
from api.strava import router as strava_router

# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="GPX Overlay Engine",
    description="""
## Moteur de synchronisation GPX ↔ Vidéo

Ce backend reçoit :
- Un fichier **GPX** (Strava, Garmin, Wahoo...)
- Les **métadonnées vidéo** de chaque clip (PAS les vidéos elles-mêmes)

Il calcule la synchronisation précise au frame près et génère
les vidéos avec les overlays configurés par le frontend.

### Flow typique :
1. `POST /gpx/upload` — Upload le GPX, récupère `session_id`
2. `POST /videos/metadata` — Envoie les métas vidéo, récupère la sync
3. `POST /videos/upload-file` — Upload physique (seulement pour le rendu)
4. `POST /render/video` — Lance le rendu avec la config widgets
5. `GET /render/status/{job_id}` — Poll jusqu'à "done"
6. `GET /render/download/{job_id}` — Télécharge la vidéo
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — permet les requêtes depuis l'app iOS (Expo / React Native)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # En prod : restreindre à ton domaine
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(router, prefix="/api/v1")
app.include_router(strava_router, prefix="/api/v1/strava")


# ─────────────────────────────────────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health():
    """Vérifie que le serveur et les dépendances sont opérationnels."""
    import subprocess
    deps = {}

    # ffprobe
    try:
        r = subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=5)
        deps["ffprobe"] = "ok" if r.returncode == 0 else "missing"
    except Exception:
        deps["ffprobe"] = "missing"

    # ffmpeg
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        deps["ffmpeg"] = "ok" if r.returncode == 0 else "missing"
    except Exception:
        deps["ffmpeg"] = "missing"

    # Pillow
    try:
        from PIL import Image
        deps["pillow"] = "ok"
    except ImportError:
        deps["pillow"] = "missing"

    all_ok = all(v == "ok" for v in deps.values())

    return JSONResponse(
        content={
            "status": "ok" if all_ok else "degraded",
            "dependencies": deps,
            "work_dir": os.environ.get("GPX_WORK_DIR", "/tmp/gpx_overlay_sessions"),
        },
        status_code=200 if all_ok else 503,
    )


@app.get("/", tags=["System"])
async def root():
    return {
        "service": "GPX Overlay Engine",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Lancement direct
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
