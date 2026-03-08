"""
Routes Strava — OAuth + récupération activités + téléchargement GPX
===================================================================
Le client_secret ne sort JAMAIS du serveur.
L'iPhone initie l'OAuth, reçoit le code via deep link,
l'envoie ici pour l'échange sécurisé.

Flow complet :
  1. GET  /strava/auth-url              → URL d'autorisation Strava
  2. POST /strava/exchange-token        → échange code → access_token
  3. GET  /strava/activities            → liste des activités
  4. GET  /strava/activity/{id}/gpx     → télécharge le GPX + upload session
"""

import os
import urllib.parse
import urllib.request
import urllib.error
import json
import tempfile
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

# ─── Config Strava (variables d'environnement) ────────────────────────────────
# Créer sur https://www.strava.com/settings/api
STRAVA_CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")

# Deep link iOS de l'app — doit correspondre au scheme dans app.json
REDIRECT_URI = os.environ.get("STRAVA_REDIRECT_URI", "gpxoverlay://strava-callback")

STRAVA_API = "https://www.strava.com/api/v3"


def _check_config():
    if not STRAVA_CLIENT_ID or not STRAVA_CLIENT_SECRET:
        raise HTTPException(
            503,
            "Strava non configuré. Définissez STRAVA_CLIENT_ID et STRAVA_CLIENT_SECRET "
            "dans les variables d'environnement du serveur."
        )


# ─── 1. URL d'autorisation ────────────────────────────────────────────────────

@router.get("/auth-url", tags=["Strava"])
async def get_auth_url():
    """
    Retourne l'URL Strava OAuth à ouvrir dans le navigateur de l'iPhone.
    L'app ouvre cette URL via expo-web-browser.
    Après autorisation, Strava redirige vers gpxoverlay://strava-callback?code=XXX
    """
    _check_config()
    params = urllib.parse.urlencode({
        "client_id":     STRAVA_CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope":         "activity:read_all",
    })
    return {
        "auth_url": f"https://www.strava.com/oauth/authorize?{params}",
        "redirect_uri": REDIRECT_URI,
    }


# ─── 2. Échange code → token ──────────────────────────────────────────────────

class TokenExchangeRequest(BaseModel):
    code: str

@router.post("/exchange-token", tags=["Strava"])
async def exchange_token(req: TokenExchangeRequest):
    """
    Reçoit le code OAuth de l'iPhone et l'échange contre un access_token.
    Le client_secret ne quitte jamais le serveur.
    Retourne : access_token, refresh_token, athlete (nom, photo)
    """
    _check_config()

    payload = urllib.parse.urlencode({
        "client_id":     STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code":          req.code,
        "grant_type":    "authorization_code",
    }).encode()

    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                "https://www.strava.com/oauth/token",
                data=payload,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ),
            timeout=10,
        ) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise HTTPException(400, f"Strava token exchange failed: {body}")

    return {
        "access_token":  data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_at":    data.get("expires_at"),
        "athlete": {
            "id":         data["athlete"]["id"],
            "firstname":  data["athlete"].get("firstname", ""),
            "lastname":   data["athlete"].get("lastname", ""),
            "profile":    data["athlete"].get("profile_medium", ""),
            "city":       data["athlete"].get("city", ""),
        },
    }


# ─── 3. Refresh token ─────────────────────────────────────────────────────────

class TokenRefreshRequest(BaseModel):
    refresh_token: str

@router.post("/refresh-token", tags=["Strava"])
async def refresh_token(req: TokenRefreshRequest):
    """Renouvelle un access_token expiré via le refresh_token."""
    _check_config()

    payload = urllib.parse.urlencode({
        "client_id":     STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "refresh_token": req.refresh_token,
        "grant_type":    "refresh_token",
    }).encode()

    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                "https://www.strava.com/oauth/token",
                data=payload,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ),
            timeout=10,
        ) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(400, f"Refresh failed: {e.read().decode()}")

    return {
        "access_token": data["access_token"],
        "expires_at":   data.get("expires_at"),
    }


# ─── 4. Liste des activités ───────────────────────────────────────────────────

@router.get("/activities", tags=["Strava"])
async def list_activities(
    access_token: str,
    page: int = 1,
    per_page: int = 30,
):
    """
    Retourne les activités Strava de l'athlète (paginées).
    Filtre sur les types avec GPS : Run, Ride, Hike, Walk, TrailRun...
    """
    params = urllib.parse.urlencode({
        "page":     page,
        "per_page": per_page,
    })
    req = urllib.request.Request(
        f"{STRAVA_API}/athlete/activities?{params}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            activities = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, f"Strava API error: {e.read().decode()}")

    # Filtre activités avec données GPS (exclude virtual, indoor)
    GPS_TYPES = {"Run", "Ride", "Hike", "Walk", "TrailRun", "VirtualRide", "NordicSki", "AlpineSki"}

    result = []
    for a in activities:
        if not a.get("start_latlng"):
            continue  # pas de GPS
        result.append({
            "id":            a["id"],
            "name":          a.get("name", ""),
            "type":          a.get("sport_type", a.get("type", "")),
            "date":          a.get("start_date_local", ""),
            "distance_km":   round(a.get("distance", 0) / 1000, 2),
            "duration_s":    a.get("moving_time", 0),
            "elevation_m":   a.get("total_elevation_gain", 0),
            "avg_hr":        a.get("average_heartrate"),
            "start_latlng":  a.get("start_latlng"),
            "map_polyline":  a.get("map", {}).get("summary_polyline", ""),
        })

    return {"activities": result, "page": page, "count": len(result)}


# ─── 5. Téléchargement GPX + création session ─────────────────────────────────

@router.post("/activity/{activity_id}/import", tags=["Strava"])
async def import_activity_gpx(activity_id: int, access_token: str):
    """
    Télécharge le GPX d'une activité Strava et crée une session GPX Overlay.
    Retourne session_id + activity_summary comme POST /gpx/upload.

    Note : l'export GPX Strava nécessite le scope activity:read_all.
    """
    # Télécharge le stream GPS de Strava (plus fiable que l'export GPX)
    streams_url = (
        f"{STRAVA_API}/activities/{activity_id}/streams"
        f"?keys=latlng,altitude,time,heartrate,cadence,watts,temp&key_by_type=true"
    )
    req = urllib.request.Request(
        streams_url,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            streams = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, f"Strava streams error: {e.read().decode()}")

    # Récupère les détails de l'activité pour la date de départ
    detail_req = urllib.request.Request(
        f"{STRAVA_API}/activities/{activity_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    with urllib.request.urlopen(detail_req, timeout=10) as resp:
        detail = json.loads(resp.read())

    # Convertit les streams en GPX
    gpx_content = _streams_to_gpx(streams, detail)

    # Sauvegarde en fichier temporaire et upload via la route GPX existante
    with tempfile.NamedTemporaryFile(suffix=".gpx", delete=False, mode="w", encoding="utf-8") as f:
        f.write(gpx_content)
        tmp_path = f.name

    # Import via le parseur GPX existant
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from core.gpx_parser import parse_gpx, get_activity_summary
    from core.data_extractor import get_available_widgets
    from api.routes import _sessions, _session_dir, WORK_DIR
    import uuid
    from datetime import datetime, timezone

    session_id = str(uuid.uuid4())
    session_dir = _session_dir(session_id)
    gpx_path = os.path.join(session_dir, "activity.gpx")

    import shutil
    shutil.move(tmp_path, gpx_path)

    points = parse_gpx(gpx_path)
    summary = get_activity_summary(points)

    _sessions[session_id] = {
        "gpx_points": points,
        "gpx_path": gpx_path,
        "summary": summary,
        "sync_result": None,
        "video_metas": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strava_activity_id": activity_id,
    }

    return {
        "session_id":        session_id,
        "activity_summary":  summary,
        "available_widgets": get_available_widgets(),
        "point_count":       len(points),
        "gpx_start":         summary["start_time"],
        "gpx_end":           summary["end_time"],
        "strava_activity": {
            "id":   activity_id,
            "name": detail.get("name", ""),
            "type": detail.get("sport_type", ""),
        },
    }


def _streams_to_gpx(streams: dict, detail: dict) -> str:
    """Convertit les streams Strava en fichier GPX valide."""
    from datetime import datetime, timezone, timedelta

    start_date = detail.get("start_date", "")
    try:
        start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
    except Exception:
        start_dt = datetime.now(timezone.utc)

    latlng    = streams.get("latlng",    {}).get("data", [])
    altitude  = streams.get("altitude",  {}).get("data", [])
    time_s    = streams.get("time",      {}).get("data", [])
    heartrate = streams.get("heartrate", {}).get("data", [])
    cadence   = streams.get("cadence",   {}).get("data", [])
    watts     = streams.get("watts",     {}).get("data", [])
    temp      = streams.get("temp",      {}).get("data", [])

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="GPX Overlay via Strava"',
        '  xmlns="http://www.topografix.com/GPX/1/1"',
        '  xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">',
        f'  <metadata><name>{detail.get("name", "Activity")}</name></metadata>',
        '  <trk><trkseg>',
    ]

    for i, ll in enumerate(latlng):
        if not ll or len(ll) < 2:
            continue
        lat, lon = ll[0], ll[1]
        ele = altitude[i] if i < len(altitude) else None
        t_offset = time_s[i] if i < len(time_s) else i
        pt_time = (start_dt + timedelta(seconds=t_offset)).strftime("%Y-%m-%dT%H:%M:%SZ")

        lines.append(f'    <trkpt lat="{lat}" lon="{lon}">')
        if ele is not None:
            lines.append(f'      <ele>{ele}</ele>')
        lines.append(f'      <time>{pt_time}</time>')

        # Extensions Garmin (FC, cadence, puissance, temp)
        ext_parts = []
        if i < len(heartrate) and heartrate[i]:
            ext_parts.append(f'<gpxtpx:hr>{heartrate[i]}</gpxtpx:hr>')
        if i < len(cadence) and cadence[i]:
            ext_parts.append(f'<gpxtpx:cad>{cadence[i]}</gpxtpx:cad>')
        if i < len(temp) and temp[i] is not None:
            ext_parts.append(f'<gpxtpx:atemp>{temp[i]}</gpxtpx:atemp>')

        if ext_parts or (i < len(watts) and watts[i]):
            lines.append('      <extensions><gpxtpx:TrackPointExtension>')
            for ep in ext_parts:
                lines.append(f'        {ep}')
            if i < len(watts) and watts[i]:
                lines.append(f'        <gpxtpx:power>{watts[i]}</gpxtpx:power>')
            lines.append('      </gpxtpx:TrackPointExtension></extensions>')

        lines.append('    </trkpt>')

    lines += ['  </trkseg></trk>', '</gpx>']
    return "\n".join(lines)
