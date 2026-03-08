"""
Routes FastAPI — GPX Overlay Engine (Option B : rendu sur iPhone)
=================================================================
Le serveur ne touche JAMAIS aux vidéos pour le rendu.
Il calcule la sync et retourne un JSON léger de données par frame.
L'iPhone rend la vidéo localement avec AVFoundation.

Routes :
  /gpx         — Upload et analyse GPX
  /videos      — Envoi des métadonnées + calcul sync
  /sync        — Export JSON frame-data pour rendu iPhone
  /sync/calibrate — Calibration GPX par point km connu dans une vidéo
  /render      — Image statique stats card (seul rendu serveur)
  /widgets     — Liste des widgets disponibles
"""

import os
import uuid
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Form
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from models.schemas import (
    GPXUploadResponse,
    VideoMetaBatchRequest,
    SyncResponse,
    VideoSyncInfo,
    RenderImageRequest,
)
from core.gpx_parser import parse_gpx, get_activity_summary
from core.sync_engine import SyncEngine, VideoMeta
from core.data_extractor import get_available_widgets, extract_widget_values
from core.renderer import render_stats_image, PIL_AVAILABLE

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# Session store (in-memory — Redis en prod)
# ─────────────────────────────────────────────────────────────────────────────

_sessions: dict[str, dict] = {}
_render_jobs: dict[str, dict] = {}   # job_id → {status, progress, output_path, error}

WORK_DIR = os.environ.get("GPX_WORK_DIR", "/tmp/gpx_overlay_sessions")
os.makedirs(WORK_DIR, exist_ok=True)

JOBS_INDEX = os.path.join(WORK_DIR, "jobs.json")


def _load_jobs_index():
    """Recharge les jobs persistés sur disque au démarrage."""
    if not os.path.exists(JOBS_INDEX):
        return
    try:
        with open(JOBS_INDEX) as f:
            saved = json.load(f)
        for job_id, job in saved.items():
            # Vérifie que le fichier de sortie existe encore
            if job.get("status") == "done" and os.path.exists(job.get("output_path", "")):
                _render_jobs[job_id] = job
    except Exception:
        pass


def _save_jobs_index():
    """Sauvegarde les jobs 'done' sur disque."""
    try:
        done_jobs = {jid: j for jid, j in _render_jobs.items() if j.get("status") == "done"}
        with open(JOBS_INDEX, "w") as f:
            json.dump(done_jobs, f)
    except Exception:
        pass


_load_jobs_index()


def _session_dir(session_id: str) -> str:
    d = os.path.join(WORK_DIR, session_id)
    os.makedirs(d, exist_ok=True)
    return d


def _get_session(session_id: str) -> dict:
    if session_id not in _sessions:
        raise HTTPException(404, f"Session '{session_id}' introuvable. Uploadez d'abord le GPX.")
    return _sessions[session_id]


# ─────────────────────────────────────────────────────────────────────────────
# 1. GPX — Upload et analyse
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/gpx/load-test", response_model=GPXUploadResponse, tags=["GPX"])
async def load_test_gpx():
    """Charge le GPX de test local (dev only — Rainy_Run_.gpx)."""
    import shutil
    test_path = "/Users/johnsanti/Downloads/GPX OVERLAY/GPX/Rainy_Run_.gpx"
    if not os.path.exists(test_path):
        raise HTTPException(404, f"Fichier test introuvable : {test_path}")
    session_id = str(uuid.uuid4())
    gpx_path = os.path.join(_session_dir(session_id), "activity.gpx")
    shutil.copy(test_path, gpx_path)
    try:
        points = parse_gpx(gpx_path)
    except Exception as e:
        raise HTTPException(500, f"Erreur parsing GPX: {e}")
    summary = get_activity_summary(points)
    _sessions[session_id] = {
        "gpx_points": points, "gpx_path": gpx_path, "summary": summary,
        "sync_result": None, "video_metas": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return GPXUploadResponse(
        session_id=session_id, activity_summary=summary,
        available_widgets=get_available_widgets(), point_count=len(points),
        gpx_start=summary["start_time"], gpx_end=summary["end_time"],
    )


@router.post("/gpx/upload", response_model=GPXUploadResponse, tags=["GPX"])
async def upload_gpx(file: UploadFile = File(...)):
    """
    Upload un fichier GPX (Strava, Garmin, Wahoo...).
    Retourne session_id + résumé activité + liste des widgets disponibles.
    """
    if not file.filename.lower().endswith(".gpx"):
        raise HTTPException(400, "Seuls les fichiers .gpx sont acceptés.")

    session_id = str(uuid.uuid4())
    session_dir = _session_dir(session_id)

    gpx_path = os.path.join(session_dir, "activity.gpx")
    content = await file.read()
    with open(gpx_path, "wb") as f:
        f.write(content)

    try:
        points = parse_gpx(gpx_path)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(500, f"Erreur parsing GPX: {e}")

    summary = get_activity_summary(points)

    _sessions[session_id] = {
        "gpx_points": points,
        "gpx_path": gpx_path,
        "summary": summary,
        "sync_result": None,
        "video_metas": {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    return GPXUploadResponse(
        session_id=session_id,
        activity_summary=summary,
        available_widgets=get_available_widgets(),
        point_count=len(points),
        gpx_start=summary["start_time"],
        gpx_end=summary["end_time"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Vidéos — Métadonnées + Sync
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/videos/metadata", response_model=SyncResponse, tags=["Videos"])
async def submit_video_metadata(request: VideoMetaBatchRequest):
    """
    Reçoit les métadonnées légères des clips (PAS les vidéos).
    L'iPhone lit ces infos via AVAsset localement et les envoie en JSON.
    Le serveur calcule la synchronisation et retourne les résultats.
    """
    session = _get_session(request.session_id)
    points = session["gpx_points"]

    video_metas = []
    for vm_req in request.videos:
        creation_time = None
        if vm_req.creation_time:
            try:
                raw = vm_req.creation_time.strip().replace("Z", "+00:00")
                creation_time = datetime.fromisoformat(raw)
                if creation_time.tzinfo is None:
                    creation_time = creation_time.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        vm = VideoMeta(
            filename=vm_req.filename,
            local_path="",
            duration_s=vm_req.duration_s,
            fps=vm_req.fps,
            width=vm_req.width,
            height=vm_req.height,
            creation_time=creation_time,
            codec=vm_req.codec,
            timezone_offset_h=vm_req.timezone_offset_h,
        )
        video_metas.append(vm)

    try:
        engine = SyncEngine(points, global_offset_s=request.global_offset_s)
        session_result = engine.sync_all(video_metas)
        report = engine.get_sync_report(session_result)
    except Exception as e:
        raise HTTPException(500, f"Erreur de synchronisation: {e}")

    session["sync_engine"] = engine
    session["sync_result"] = session_result
    session["video_metas"] = {vm.filename: vm for vm in video_metas}

    videos_info = [VideoSyncInfo(**v) for v in report["videos"]]

    return SyncResponse(
        session_id=request.session_id,
        gpx_start=report["gpx_start"],
        gpx_end=report["gpx_end"],
        gpx_duration_s=report["gpx_duration_s"],
        global_offset_s=report["global_offset_s"],
        video_count=report["video_count"],
        videos=videos_info,
        warnings=report["warnings"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. CŒUR OPTION B — Export JSON frame-data pour rendu iPhone
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/sync/frame-data/{session_id}/{filename}", tags=["Sync"])
async def get_frame_data(
    session_id: str,
    filename: str,
    offset_s: float = 0.0,
    sample_rate: int = 1,
):
    """
    Retourne les données GPX frame par frame pour un clip vidéo.
    L'iPhone utilise ce JSON pour dessiner les widgets localement via AVFoundation.

    Paramètres :
      offset_s    : ajustement manuel de sync en secondes
      sample_rate : 1 = toutes les frames, 2 = 1 frame sur 2, etc.
                    (utile pour la preview temps réel avant export final)

    Format de réponse optimisé (clés courtes pour minimiser la taille) :
      t  = temps dans la vidéo (secondes)
      sp = vitesse km/h
      pa = allure secondes/km
      hr = fréquence cardiaque bpm
      sl = pente %
      el = altitude m
      di = distance depuis départ m
      ca = cadence spm
      pw = puissance W
      te = température °C
      la = latitude
      lo = longitude
      be = cap degrés
      dg = D+ cumulé m
    """
    session = _get_session(session_id)
    sync_result_session = session.get("sync_result")

    if sync_result_session is None:
        raise HTTPException(400, "Sync non effectuée. Appelez d'abord POST /videos/metadata.")

    # Retrouve le VideoSyncResult pour ce clip
    video_sync = None
    for vr in sync_result_session.videos:
        if vr.video.filename == filename:
            video_sync = vr
            break

    if video_sync is None:
        raise HTTPException(404, f"Clip '{filename}' non trouvé dans la session.")

    # Applique un offset manuel si demandé
    if offset_s != 0.0:
        engine = session["sync_engine"]
        vm = session["video_metas"][filename]
        video_sync = engine.sync_video(vm, manual_offset_s=offset_s)

    if not video_sync.has_data:
        raise HTTPException(
            400,
            f"Clip '{filename}' : aucune donnée GPX couverte (coverage: {video_sync.coverage_pct}%). "
            f"Ajustez l'offset ou vérifiez l'heure de création de la vidéo."
        )

    # Calcul D+ cumulé
    from core.data_extractor import compute_elevation_gain_series
    compute_elevation_gain_series(video_sync.frame_data)

    gpx_start = session["gpx_points"][0].time

    # ── Lissage des valeurs (moyenne glissante) ──────────────────────────
    # Évite les oscillations visuelles dues à la résolution GPS (1pt/sec)
    # Les valeurs restent réactives mais ne "sautent" pas à chaque frame.
    # 3 secondes de lissage = efface le bruit GPS (1pt/sec) sans lag visible
    _smooth_frame_data(video_sync.frame_data, window=int(video_sync.video.fps * 3))

    # Construction du JSON compact
    frames = []
    for fd in video_sync.frame_data:
        if fd.frame_index % sample_rate != 0:
            continue

        frame = {"t": round(fd.video_time_s, 4)}

        if fd.speed_kmh is not None:
            frame["sp"] = round(fd.speed_kmh, 2)
        if fd.pace_s_per_km is not None and 60 < fd.pace_s_per_km < 1800:
            frame["pa"] = round(fd.pace_s_per_km, 1)
        if fd.heart_rate is not None:
            frame["hr"] = fd.heart_rate
        if fd.slope_pct is not None:
            frame["sl"] = round(fd.slope_pct, 2)
        if fd.elevation_m is not None:
            frame["el"] = round(fd.elevation_m, 1)
        if fd.distance_m is not None:
            frame["di"] = round(fd.distance_m, 1)
        if fd.cadence is not None:
            frame["ca"] = fd.cadence
        if fd.power is not None:
            frame["pw"] = fd.power
        if fd.temperature is not None:
            frame["te"] = round(fd.temperature, 1)
        if fd.lat is not None:
            frame["la"] = round(fd.lat, 6)
        if fd.lon is not None:
            frame["lo"] = round(fd.lon, 6)
        if fd.bearing is not None:
            frame["be"] = round(fd.bearing, 1)
        if fd.elevation_gain_so_far > 0:
            frame["dg"] = round(fd.elevation_gain_so_far, 1)

        frames.append(frame)

    video = video_sync.video
    elapsed_start = (video_sync.gpx_segment_start - gpx_start).total_seconds() if video_sync.gpx_segment_start else 0

    # Heure locale au début du clip (pour affichage frontend)
    local_start_time = None
    if video_sync.gpx_segment_start and video.timezone_offset_h is not None:
        from datetime import timedelta as _td
        local_dt = video_sync.gpx_segment_start + _td(hours=video.timezone_offset_h)
        local_start_time = local_dt.strftime("%H:%M:%S")

    response = {
        "filename": filename,
        "fps": video.fps,
        "duration_s": video.duration_s,
        "width": video.width,
        "height": video.height,
        "gpx_segment_start": video_sync.gpx_segment_start.isoformat() if video_sync.gpx_segment_start else None,
        "gpx_segment_end": video_sync.gpx_segment_end.isoformat() if video_sync.gpx_segment_end else None,
        "local_start_time": local_start_time,        # heure locale affichée dans l'app
        "timezone_offset_h": video.timezone_offset_h, # ex: -5.0 pour NYC EST
        "elapsed_start_s": round(elapsed_start, 2),
        "coverage_pct": video_sync.coverage_pct,
        "sync_confidence": round(video.sync_confidence, 2),
        "sample_rate": sample_rate,
        "frame_count": len(frames),
        # Légende des clés (pour le debug frontend)
        "keys": {
            "t": "video_time_s", "sp": "speed_kmh", "pa": "pace_s_per_km",
            "hr": "heart_rate_bpm", "sl": "slope_pct", "el": "elevation_m",
            "di": "distance_m", "ca": "cadence_spm", "pw": "power_w",
            "te": "temperature_c", "la": "latitude", "lo": "longitude",
            "be": "bearing_deg", "dg": "elev_gain_m"
        },
        "frames": frames,
    }

    return JSONResponse(content=response)


@router.get("/sync/preview/{session_id}/{filename}", tags=["Sync"])
async def get_preview_frame_data(session_id: str, filename: str, offset_s: float = 0.0):
    """
    Version allégée de frame-data pour la preview temps réel.
    Retourne 1 frame par seconde (au lieu de 30) → 30x plus léger.
    Parfait pour afficher un aperçu pendant que l'utilisateur ajuste les widgets.
    """
    video = None
    session = _get_session(session_id)
    sync_result_session = session.get("sync_result")
    if sync_result_session:
        for vr in sync_result_session.videos:
            if vr.video.filename == filename:
                video = vr.video
                break

    fps = video.fps if video else 30.0
    sample_rate = max(1, int(fps))  # 1 sample par seconde

    # Redirige vers frame-data avec sample_rate élevé
    return await get_frame_data(session_id, filename, offset_s, sample_rate=sample_rate)


@router.post("/sync/adjust-offset", tags=["Sync"])
async def adjust_offset(
    session_id: str,
    filename: str,
    offset_s: float,
):
    """
    Recalcule la sync avec un nouvel offset (ajustement manuel de l'utilisateur).
    Retourne juste le rapport de sync (coverage, confidence) sans les frames.
    L'utilisateur peut ainsi affiner l'offset avant de télécharger les frames complètes.
    """
    session = _get_session(session_id)
    engine = session.get("sync_engine")
    vm = session.get("video_metas", {}).get(filename)

    if engine is None or vm is None:
        raise HTTPException(400, "Sync non effectuée.")

    result = engine.sync_video(vm, manual_offset_s=offset_s)

    return {
        "filename": filename,
        "offset_s": offset_s,
        "coverage_pct": result.coverage_pct,
        "sync_confidence": round(vm.sync_confidence, 2),
        "has_data": result.has_data,
        "gpx_segment_start": result.gpx_segment_start.isoformat() if result.gpx_segment_start else None,
        "gpx_segment_end": result.gpx_segment_end.isoformat() if result.gpx_segment_end else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3b. Calibration — Calcul automatique de l'offset via point km connu
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/sync/calibrate", tags=["Sync"])
async def calibrate_offset(
    session_id: str,
    filename: str,
    video_time_s: float,
    known_distance_m: float,
):
    """
    Calcule automatiquement l'offset de synchronisation GPX ↔ vidéo
    à partir d'un événement connu dans les deux timelines.

    Méthode :
      1. L'utilisateur filme sa montre au moment où elle annonce "X km"
      2. Il note le timestamp dans la vidéo (ex: 12.5s dans IMG_5991.MOV)
      3. Il envoie : filename, video_time_s=12.5, known_distance_m=7000

    Calcul :
      - On cherche dans le GPX quand la distance known_distance_m est atteinte
        → gpx_event_time (timestamp UTC absolu)
      - On calcule l'heure UTC absolue de cet événement dans la vidéo :
        → video_event_utc = clip.creation_time + video_time_s
      - L'offset = gpx_event_time - video_event_utc

    Exemple :
      known_distance_m = 7000
      → GPX atteint 7000m à 13:52:30 UTC
      → Vidéo : création 13:53:25 UTC + 12.5s dans la vidéo = 13:53:37.5 UTC
      → offset = 13:52:30 - 13:53:37.5 = -67.5s
      → SyncEngine appliquera cet offset à tous les clips de la session
    """
    from datetime import timedelta
    import bisect

    session = _get_session(session_id)
    points = session["gpx_points"]
    vm = session.get("video_metas", {}).get(filename)

    if vm is None:
        raise HTTPException(
            400,
            f"Clip '{filename}' non trouvé. Appelez d'abord POST /videos/metadata."
        )

    if not vm.creation_time:
        raise HTTPException(
            400,
            "La vidéo n'a pas de métadonnée creation_time. Impossible de calibrer."
        )

    if known_distance_m <= 0 or known_distance_m > 200_000:
        raise HTTPException(400, "known_distance_m doit être entre 1 et 200 000 mètres.")

    if video_time_s < 0 or video_time_s > vm.duration_s:
        raise HTTPException(
            400,
            f"video_time_s={video_time_s} hors de la durée du clip ({vm.duration_s:.1f}s)."
        )

    # ── 1. Trouver quand le GPX atteint known_distance_m ─────────────────────
    # Les points GPX ont distance_m (cumulative depuis le départ)
    distances = [p.distance_m for p in points if p.distance_m is not None]
    times     = [p.time       for p in points if p.distance_m is not None]
    pts_with_dist = [(p.distance_m, p.time) for p in points if p.distance_m is not None]

    if not pts_with_dist:
        raise HTTPException(500, "Aucun point GPX avec distance calculée.")

    max_dist = pts_with_dist[-1][0]
    if known_distance_m > max_dist:
        raise HTTPException(
            400,
            f"Distance {known_distance_m:.0f}m dépasse la distance GPX totale ({max_dist:.0f}m)."
        )

    # Interpolation linéaire pour trouver l'heure exacte à known_distance_m
    gpx_event_time = None
    for i in range(1, len(pts_with_dist)):
        d0, t0 = pts_with_dist[i - 1]
        d1, t1 = pts_with_dist[i]
        if d0 <= known_distance_m <= d1:
            if d1 == d0:
                gpx_event_time = t0
            else:
                frac = (known_distance_m - d0) / (d1 - d0)
                delta = (t1 - t0).total_seconds() * frac
                gpx_event_time = t0 + timedelta(seconds=delta)
            break

    if gpx_event_time is None:
        raise HTTPException(500, "Impossible d'interpoler l'heure pour cette distance.")

    # ── 2. Calculer l'heure UTC absolue de l'événement dans la vidéo ─────────
    video_event_utc = vm.creation_time + timedelta(seconds=video_time_s)

    # ── 3. Offset = heure GPX - heure vidéo ──────────────────────────────────
    offset_s = (gpx_event_time - video_event_utc).total_seconds()

    # ── 4. Mettre à jour le moteur de sync avec ce nouvel offset ─────────────
    engine = session.get("sync_engine")
    if engine:
        engine.global_offset_s = offset_s
        # Recalcule la sync pour tous les clips
        video_metas = list(session["video_metas"].values())
        session["sync_result"] = engine.sync_all(video_metas)

    # ── 5. Réponse avec détails de calibration ────────────────────────────────
    return {
        "offset_s": round(offset_s, 2),
        "calibration": {
            "clip": filename,
            "video_time_s": video_time_s,
            "known_distance_m": known_distance_m,
            "gpx_event_time_utc": gpx_event_time.isoformat(),
            "video_event_utc": video_event_utc.isoformat(),
        },
        "description": (
            f"À {video_time_s:.1f}s dans {filename}, la montre annonce {known_distance_m/1000:.1f}km. "
            f"Le GPX atteint cette distance à {gpx_event_time.strftime('%H:%M:%S')} UTC. "
            f"Offset calculé : {offset_s:+.2f}s."
        ),
        "message": "Offset appliqué à tous les clips de la session. Rappellez /sync/frame-data pour les nouvelles frames.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. Image statique — Stats Card (seul rendu côté serveur)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/render/stats-image", tags=["Render"])
async def render_stats_card(
    request: RenderImageRequest,
    background_photo: Optional[UploadFile] = File(None),
):
    """
    Génère une image JPEG avec les stats de l'activité.
    Style Strava — photo de fond optionnelle.
    C'est le seul rendu fait côté serveur (image légère, pas vidéo).
    """
    session = _get_session(request.session_id)
    summary = session["summary"]
    session_dir = _session_dir(request.session_id)

    bg_path = None
    if background_photo:
        bg_path = os.path.join(session_dir, "background.jpg")
        content = await background_photo.read()
        with open(bg_path, "wb") as f:
            f.write(content)

    output_path = os.path.join(session_dir, f"stats_{uuid.uuid4().hex[:8]}.jpg")

    try:
        render_stats_image(
            activity_summary=summary,
            widget_keys=request.widget_keys,
            output_path=output_path,
            background_image_path=bg_path,
            width=request.width,
            height=request.height,
        )
    except Exception as e:
        raise HTTPException(500, f"Erreur génération image: {e}")

    return FileResponse(output_path, media_type="image/jpeg", filename="stats_card.jpg")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Widgets
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 4b. Preview frame — PNG base64 avec widgets positionnés
# ─────────────────────────────────────────────────────────────────────────────

class _PreviewWidgetItem(BaseModel):
    key: str
    x: float   # 0..1
    y: float   # 0..1
    anchor: str = "top-left"

class _PreviewFrameRequest(BaseModel):
    session_id: str
    widget_layout: list[_PreviewWidgetItem]
    filename: Optional[str] = None   # None → premier clip avec données
    frame_time_s: float = 5.0        # secondes dans la vidéo
    canvas_width: int = 390
    canvas_height: int = 844


@router.post("/render/preview-frame", tags=["Render"])
async def render_preview_frame(request: _PreviewFrameRequest):
    """
    Rend un unique PNG (base64) avec les widgets positionnés.
    Utilisé par le frontend pour la preview du layout.
    Retourne { image_b64: "...", width: 390, height: 844 }
    """
    import base64, io

    if not PIL_AVAILABLE:
        raise HTTPException(500, "Pillow non installé sur le serveur.")

    session = _get_session(request.session_id)
    sync_result_session = session.get("sync_result")

    if sync_result_session is None:
        raise HTTPException(400, "Sync non effectuée. Appelez d'abord POST /videos/metadata.")

    # ── Trouver le bon VideoSyncResult ────────────────────────────────────────
    video_sync = None
    if request.filename:
        for vr in sync_result_session.videos:
            if vr.video.filename == request.filename and vr.has_data:
                video_sync = vr
                break
    else:
        # Premier clip avec données
        for vr in sync_result_session.videos:
            if vr.has_data:
                video_sync = vr
                break

    if video_sync is None or not video_sync.frame_data:
        raise HTTPException(400, "Aucune donnée GPX disponible pour ce clip.")

    # ── Trouver le FrameData le plus proche de frame_time_s ───────────────────
    target_t = request.frame_time_s
    best_fd = min(video_sync.frame_data, key=lambda fd: abs(fd.video_time_s - target_t))

    # ── Préparer les widgets ──────────────────────────────────────────────────
    from core.renderer import VideoRenderer, RenderConfig, WidgetConfig, WidgetPosition, WidgetStyle
    from core.data_extractor import compute_elevation_gain_series

    compute_elevation_gain_series(video_sync.frame_data)

    gpx_start = session["gpx_points"][0].time
    widget_keys = [w.key for w in request.widget_layout]
    values = extract_widget_values(best_fd, widget_keys, gpx_start)

    # ── Dessiner sur canvas PIL ───────────────────────────────────────────────
    w = request.canvas_width
    h = request.canvas_height

    renderer = VideoRenderer(RenderConfig(widgets=[]), gpx_start=gpx_start)

    active_widgets = []
    for wl in request.widget_layout:
        wv = values.get(wl.key)
        if wv is None or not wv.available:
            continue
        active_widgets.append(WidgetConfig(
            key=wl.key,
            position=WidgetPosition(x=wl.x, y=wl.y, anchor=wl.anchor),
            style=WidgetStyle(font_size=32, padding=12),
        ))

    overlay = renderer._draw_overlay(w, h, active_widgets, values)

    # Fond sombre simulant la vidéo
    from PIL import Image
    bg = Image.new("RGBA", (w, h), (20, 20, 20, 255))
    bg.alpha_composite(overlay)
    bg = bg.convert("RGB")

    buf = io.BytesIO()
    bg.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()

    return {"image_b64": b64, "width": w, "height": h}


# ─────────────────────────────────────────────────────────────────────────────
# 5. Rendu vidéo serveur (mode dev sans build natif)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/videos/upload-and-probe/{session_id}", tags=["Videos"])
async def upload_and_probe(session_id: str, file: UploadFile = File(...)):
    """
    Upload une vidéo, l'enregistre dans la session, et extrait ses métadonnées
    via ffprobe (creation_time, fps, duration, dimensions).
    Utilisé par step 2 en mode dev (Expo Go) à la place d'expo-image-picker.
    La vidéo reste stockée → step 5 n'a pas besoin de la re-uploader.
    """
    import subprocess as _sp, json as _json, fractions as _frac

    _get_session(session_id)
    session_dir = _session_dir(session_id)

    filename = file.filename or f"video_{uuid.uuid4().hex[:8]}.mov"
    video_path = os.path.join(session_dir, filename)

    content = await file.read()
    with open(video_path, "wb") as f:
        f.write(content)

    # ── ffprobe pour extraction métadonnées ───────────────────────────────────
    meta = {"filename": filename, "video_path": video_path}

    try:
        r = _sp.run([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate,nb_frames,duration,width,height,side_data_list:stream_side_data=rotation:stream_tags=rotate:format_tags=creation_time",
            "-of", "json", video_path,
        ], capture_output=True, text=True, timeout=20)

        data = _json.loads(r.stdout)
        stream = data.get("streams", [{}])[0]
        fmt_tags = data.get("format", {}).get("tags", {})

        # FPS
        fps_str = stream.get("r_frame_rate", "30/1")
        if "/" in fps_str:
            n, d = fps_str.split("/")
            fps = int(n) / int(d)
        else:
            fps = float(fps_str)

        # Duration
        duration_s = float(stream.get("duration") or 0)

        # Frame count
        nb = stream.get("nb_frames")
        total_frames = int(nb) if nb and int(nb) > 0 else round(duration_s * fps)

        # Dimensions brutes
        width = int(stream.get("width") or 1080)
        height = int(stream.get("height") or 1920)

        # Rotation — cherche dans tags OU directement dans side_data
        rotate = 0
        stream_tags = stream.get("tags", {})
        if "rotate" in stream_tags:
            rotate = int(stream_tags["rotate"])
        else:
            for sd in stream.get("side_data_list", []):
                if "rotation" in sd:
                    rotate = int(sd["rotation"])
                    break
        if abs(rotate) in (90, 270):
            width, height = height, width  # swap pour dimensions d'affichage réelles

        # creation_time (QuickTime metadata)
        creation_time = fmt_tags.get("creation_time")

        meta.update({
            "fps": round(fps, 4),
            "duration_s": round(duration_s, 3),
            "total_frames": total_frames,
            "width": width,
            "height": height,
            "rotate": rotate,
            "creation_time": creation_time,
            "file_size_mb": round(len(content) / 1_048_576, 1),
        })

    except Exception as e:
        meta["probe_error"] = str(e)
        meta.update({"fps": 30.0, "duration_s": 0.0, "total_frames": 0,
                     "width": 1080, "height": 1920, "creation_time": None})

    # Stocke le local_path dans video_metas si le clip existe déjà
    vm_dict = _get_session(session_id).get("video_metas", {})
    if filename in vm_dict:
        vm_dict[filename].local_path = video_path

    return meta


@router.get("/render/check-video/{session_id}/{filename}", tags=["Render"])
async def check_video_exists(session_id: str, filename: str):
    """Vérifie si une vidéo est déjà stockée sur le serveur pour cette session."""
    _get_session(session_id)
    video_path = os.path.join(_session_dir(session_id), filename)
    return {"exists": os.path.exists(video_path), "filename": filename}


@router.post("/render/upload-video/{session_id}", tags=["Render"])
async def upload_video_for_render(session_id: str, file: UploadFile = File(...)):
    """
    Upload la vidéo physique pour le rendu serveur.
    Retourne le filename confirmé.
    À utiliser uniquement en mode dev (pas de build natif).
    """
    session = _get_session(session_id)
    session_dir = _session_dir(session_id)

    filename = file.filename or f"video_{uuid.uuid4().hex[:8]}.mov"
    video_path = os.path.join(session_dir, filename)

    content = await file.read()
    with open(video_path, "wb") as f:
        f.write(content)

    # Met à jour le local_path dans video_metas si le clip existe déjà
    vm_dict = session.get("video_metas", {})
    if filename in vm_dict:
        vm_dict[filename].local_path = video_path

    file_size_mb = len(content) / 1_048_576
    return {
        "session_id": session_id,
        "filename": filename,
        "video_path": video_path,
        "file_size_mb": round(file_size_mb, 1),
    }


class _RenderStartRequest(BaseModel):
    session_id: str
    filename: str
    widget_layout: list[_PreviewWidgetItem]
    offset_s: float = 0.0
    quality: str = "medium"    # low | medium | high


@router.post("/render/start", tags=["Render"])
async def start_render(request: _RenderStartRequest, background_tasks: BackgroundTasks):
    """
    Lance le rendu vidéo en arrière-plan.
    Retourne un job_id pour suivre la progression.
    """
    session = _get_session(request.session_id)
    session_dir = _session_dir(request.session_id)

    video_path = os.path.join(session_dir, request.filename)
    if not os.path.exists(video_path):
        raise HTTPException(
            400,
            f"Vidéo '{request.filename}' non uploadée. Appelez d'abord /render/upload-video."
        )

    sync_result_session = session.get("sync_result")
    if sync_result_session is None:
        raise HTTPException(400, "Sync non effectuée.")

    # Trouve le VideoSyncResult
    video_sync = None
    for vr in sync_result_session.videos:
        if vr.video.filename == request.filename:
            video_sync = vr
            break
    if video_sync is None:
        raise HTTPException(404, f"Clip '{request.filename}' non trouvé dans la session.")

    # Met à jour le local_path
    video_sync.video.local_path = video_path

    job_id = uuid.uuid4().hex[:12]
    output_path = os.path.join(session_dir, f"rendered_{job_id}.mp4")

    _render_jobs[job_id] = {
        "status": "queued",
        "progress_pct": 0.0,
        "output_path": output_path,
        "filename": request.filename,
        "error": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    background_tasks.add_task(
        _run_render_job,
        job_id=job_id,
        video_sync=video_sync,
        widget_layout=request.widget_layout,
        output_path=output_path,
        quality=request.quality,
        gpx_points=session["gpx_points"],
    )

    return {"job_id": job_id, "status": "queued"}


async def _run_render_job(job_id, video_sync, widget_layout, output_path, quality, gpx_points):
    """Exécute le rendu FFmpeg en arrière-plan."""
    from core.renderer import VideoRenderer, RenderConfig, WidgetConfig, WidgetPosition, WidgetStyle
    from core.data_extractor import compute_elevation_gain_series

    job = _render_jobs[job_id]
    job["status"] = "processing"
    job["progress_pct"] = 5.0

    try:
        compute_elevation_gain_series(video_sync.frame_data)

        active_widgets = [
            WidgetConfig(
                key=w.key,
                position=WidgetPosition(x=w.x, y=w.y, anchor=w.anchor),
                style=WidgetStyle(font_size=36, padding=14),
            )
            for w in widget_layout
        ]

        render_config = RenderConfig(widgets=active_widgets, output_quality=quality)
        renderer = VideoRenderer(render_config, gpx_start=gpx_points[0].time)

        # Le rendu est bloquant (ffmpeg subprocess) → on le lance dans un thread
        loop = asyncio.get_event_loop()

        def do_render():
            # Callback de progression basique via comptage frames
            job["progress_pct"] = 10.0
            result = renderer.render(video_sync, output_path)
            job["progress_pct"] = 95.0
            return result

        await loop.run_in_executor(None, do_render)

        job["status"] = "done"
        job["progress_pct"] = 100.0
        _save_jobs_index()

    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@router.get("/render/status/{job_id}", tags=["Render"])
async def get_render_status(job_id: str):
    """Statut d'un job de rendu — poller toutes les 2 secondes."""
    if job_id not in _render_jobs:
        raise HTTPException(404, f"Job '{job_id}' introuvable.")
    job = _render_jobs[job_id]
    return {
        "job_id": job_id,
        "status": job["status"],
        "progress_pct": job["progress_pct"],
        "error": job["error"],
        "filename": job["filename"],
    }


@router.get("/render/download/{job_id}", tags=["Render"])
async def download_rendered_video(job_id: str):
    """Télécharge la vidéo rendue (une fois status='done')."""
    if job_id not in _render_jobs:
        raise HTTPException(404, "Job introuvable.")
    job = _render_jobs[job_id]
    if job["status"] != "done":
        raise HTTPException(400, f"Rendu pas encore terminé (status: {job['status']}).")
    output_path = job["output_path"]
    if not os.path.exists(output_path):
        raise HTTPException(500, "Fichier rendu introuvable.")
    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"gpx_overlay_{job['filename'].replace('.MOV', '').replace('.mov', '')}.mp4",
    )


@router.get("/widgets", tags=["Widgets"])
async def list_widgets():
    """Liste tous les widgets disponibles. Le frontend construit son UI depuis ça."""
    return {"widgets": get_available_widgets()}


@router.get("/widgets/preview/{session_id}/{widget_key}", tags=["Widgets"])
async def preview_widget(session_id: str, widget_key: str):
    """Valeur d'un widget au milieu de l'activité — pour la preview de sélection."""
    session = _get_session(session_id)
    points = session["gpx_points"]

    if not points:
        raise HTTPException(404, "Aucun point GPX.")

    mid_point = points[len(points) // 2]

    from core.sync_engine import FrameData

    fd = FrameData(
        frame_index=0,
        video_time_s=0.0,
        gpx_time=mid_point.time,
        speed_kmh=mid_point.speed_kmh,
        speed_ms=mid_point.speed_ms,
        pace_s_per_km=mid_point.pace_s_per_km,
        slope_pct=mid_point.slope_pct,
        elevation_m=mid_point.elevation,
        distance_m=mid_point.distance_m,
        heart_rate=mid_point.heart_rate,
        cadence=mid_point.cadence,
        power=mid_point.power,
        temperature=mid_point.temperature,
        lat=mid_point.lat,
        lon=mid_point.lon,
        bearing=mid_point.bearing,
        distance_from_start_km=(mid_point.distance_m or 0) / 1000,
    )

    values = extract_widget_values(fd, [widget_key], gpx_start=points[0].time)
    wv = values.get(widget_key)

    if not wv:
        raise HTTPException(404, f"Widget '{widget_key}' non disponible.")

    return {
        "key": wv.key,
        "label": wv.label,
        "value": wv.value,
        "unit": wv.unit,
        "raw": wv.raw,
        "available": wv.available,
        "color_hint": wv.color_hint,
    }


def _smooth_frame_data(frames: list, window: int = 30):
    """
    Lissage par moyenne glissante sur les métriques numériques.
    window = nombre de frames sur lesquelles on moyenne.
    À 60fps, window=48 = 0.8 seconde de lissage → fluide sans lag perceptible.

    Valeurs lissées : vitesse, allure, pente (les plus "sautillantes")
    Valeurs NON lissées : FC, altitude, distance (déjà stables ou doivent être précises)
    """
    if not frames or window < 2:
        return

    n = len(frames)
    half = window // 2

    speeds = [f.speed_kmh     for f in frames]
    paces  = [f.pace_s_per_km for f in frames]
    slopes = [f.slope_pct     for f in frames]

    def moving_avg(series, i):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        vals = [v for v in series[lo:hi] if v is not None]
        return sum(vals) / len(vals) if vals else None

    for i, fd in enumerate(frames):
        fd.speed_kmh     = moving_avg(speeds, i)
        fd.pace_s_per_km = moving_avg(paces, i)
        fd.slope_pct     = moving_avg(slopes, i)
        if fd.speed_kmh is not None:
            fd.speed_ms = fd.speed_kmh / 3.6
