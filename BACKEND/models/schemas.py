"""
Schemas Pydantic — Contrats API entre Frontend et Backend
==========================================================
Ces modèles définissent exactement ce que l'iPhone envoie
et ce que le serveur retourne. Zéro ambiguïté.
"""

from pydantic import BaseModel, Field, validator
from typing import Optional, List
from datetime import datetime
from enum import Enum


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class AnchorEnum(str, Enum):
    top_left     = "top-left"
    top_center   = "top-center"
    top_right    = "top-right"
    center_left  = "center-left"
    center       = "center"
    center_right = "center-right"
    bottom_left  = "bottom-left"
    bottom_center= "bottom-center"
    bottom_right = "bottom-right"


class QualityEnum(str, Enum):
    low      = "low"
    medium   = "medium"
    high     = "high"
    lossless = "lossless"


class OutputFormatEnum(str, Enum):
    mp4 = "mp4"
    mov = "mov"


# ─────────────────────────────────────────────────────────────────────────────
# Upload GPX
# ─────────────────────────────────────────────────────────────────────────────

class GPXUploadResponse(BaseModel):
    session_id: str
    activity_summary: dict
    available_widgets: List[dict]
    point_count: int
    gpx_start: str    # ISO datetime
    gpx_end: str      # ISO datetime


# ─────────────────────────────────────────────────────────────────────────────
# Métadonnées vidéo (envoyées par l'iPhone, PAS la vidéo elle-même)
# ─────────────────────────────────────────────────────────────────────────────

class VideoMetaRequest(BaseModel):
    """
    Ce que l'iPhone envoie pour chaque clip vidéo.
    Récupéré côté iOS via AVAsset / PHAsset.
    """
    filename: str = Field(..., description="Nom du fichier (ex: IMG_0042.MOV)")
    duration_s: float = Field(..., gt=0, description="Durée en secondes")
    fps: float = Field(default=30.0, description="Frames par seconde")
    width: int = Field(default=1920)
    height: int = Field(default=1080)
    creation_time: Optional[str] = Field(
        None,
        description="ISO 8601 UTC — heure de début d'enregistrement"
    )
    codec: str = Field(default="h264")
    file_size_bytes: Optional[int] = Field(None, description="Taille fichier (info)")
    timezone_offset_h: float = Field(
        default=0.0,
        description="Offset TZ de l'iPhone en heures (ex: 2.0 pour CEST)"
    )


class VideoMetaBatchRequest(BaseModel):
    """Batch de métadonnées vidéo pour une session."""
    session_id: str
    videos: List[VideoMetaRequest]
    global_offset_s: float = Field(
        default=0.0,
        description="Offset global à appliquer à tous les clips (ajustement manuel)"
    )


class VideoSyncInfo(BaseModel):
    """Résultat de sync pour un clip — renvoyé au frontend."""
    filename: str
    duration_s: float
    fps: float
    resolution: str
    creation_time: Optional[str]
    gpx_segment_start: Optional[str]
    gpx_segment_end: Optional[str]
    coverage_pct: float
    has_data: bool
    sync_method: str
    sync_confidence: float
    frame_count: int


class SyncResponse(BaseModel):
    """Réponse complète de l'analyse de sync."""
    session_id: str
    gpx_start: str
    gpx_end: str
    gpx_duration_s: float
    global_offset_s: float
    video_count: int
    videos: List[VideoSyncInfo]
    warnings: List[str]


# ─────────────────────────────────────────────────────────────────────────────
# Configuration widgets (du frontend)
# ─────────────────────────────────────────────────────────────────────────────

class WidgetPositionRequest(BaseModel):
    x: float = Field(..., ge=0.0, le=1.0, description="Position X (0=gauche, 1=droite)")
    y: float = Field(..., ge=0.0, le=1.0, description="Position Y (0=haut, 1=bas)")
    anchor: AnchorEnum = AnchorEnum.top_left


class WidgetStyleRequest(BaseModel):
    font_size: int = Field(default=48, ge=12, le=200)
    font_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    bg_color: str = Field(default="#00000088", description="#RRGGBBAA")
    border_radius: int = Field(default=12, ge=0, le=100)
    padding: int = Field(default=16, ge=0, le=100)
    show_label: bool = True
    show_unit: bool = True
    use_color_hint: bool = True
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    font_path: Optional[str] = None


class WidgetConfigRequest(BaseModel):
    key: str = Field(..., description="Identifiant du widget (ex: 'speed', 'hr')")
    position: WidgetPositionRequest
    style: WidgetStyleRequest = Field(default_factory=WidgetStyleRequest)
    visible: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# Requête de rendu vidéo
# ─────────────────────────────────────────────────────────────────────────────

class RenderVideoRequest(BaseModel):
    """
    Tout ce dont le backend a besoin pour lancer un rendu.
    La vidéo physique doit déjà être uploadée sur le serveur.
    """
    session_id: str
    filename: str                        # identifie le clip à rendre
    widgets: List[WidgetConfigRequest]
    output_format: OutputFormatEnum = OutputFormatEnum.mp4
    output_quality: QualityEnum = QualityEnum.high
    output_fps: Optional[float] = Field(None, ge=1.0, le=120.0)
    output_resolution: Optional[List[int]] = Field(
        None,
        description="[width, height] ou null pour conserver la résolution source"
    )
    manual_offset_s: float = Field(
        default=0.0,
        description="Ajustement fin du timing pour ce clip précis"
    )


class RenderResponse(BaseModel):
    job_id: str
    status: str          # "queued" | "processing" | "done" | "error"
    download_url: Optional[str] = None
    progress_pct: float = 0.0
    error: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# Requête image statique (stats card)
# ─────────────────────────────────────────────────────────────────────────────

class RenderImageRequest(BaseModel):
    session_id: str
    widget_keys: List[str] = Field(
        default=["distance", "speed", "pace", "elevation", "hr", "time_elapsed"],
        description="Métriques à afficher sur la carte"
    )
    width: int = Field(default=1080, ge=256, le=4096)
    height: int = Field(default=1080, ge=256, le=4096)
    has_background_photo: bool = Field(
        default=False,
        description="True si une photo de fond a été uploadée"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Statut job (rendu asynchrone)
# ─────────────────────────────────────────────────────────────────────────────

class JobStatus(BaseModel):
    job_id: str
    status: str
    progress_pct: float
    download_url: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str
