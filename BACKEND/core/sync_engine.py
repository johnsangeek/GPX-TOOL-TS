"""
SYNC ENGINE — Le Cœur du Moteur GPX Overlay
============================================
Principe fondateur :
  • L'iPhone envoie UNIQUEMENT les métadonnées vidéo (pas les vidéos elles-mêmes)
  • Le moteur lit la creation_time de chaque clip via ffprobe
  • Il positionne chaque clip sur la timeline GPX au milliseconde près
  • Pour chaque frame de chaque vidéo → interpolation des métriques GPX exactes

Architecture :
  VideoMeta (métadonnées légères depuis iPhone)
    ↓
  SyncEngine.analyze_videos(gpx_points, video_metas)
    ↓
  SyncResult par vidéo = { offset_in_gpx, frame_data[], coverage_pct }
    ↓
  Renderer reçoit les SyncResults → applique les widgets
"""

import subprocess
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
import math

from .gpx_parser import TrackPoint


# ─────────────────────────────────────────────────────────────────────────────
# Modèles de données
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VideoMeta:
    """
    Métadonnées légères d'un clip vidéo.
    Envoyées par l'iPhone — PAS le fichier vidéo entier.
    """
    filename: str
    local_path: str                          # chemin local sur le serveur (après upload léger)
    duration_s: float                        # durée en secondes
    fps: float                               # frames par seconde
    width: int
    height: int
    creation_time: Optional[datetime] = None # heure de création UTC (EXIF/metadata)
    codec: str = "unknown"
    timezone_offset_h: float = 0.0          # offset TZ de l'iPhone (ex: +2.0 pour CEST)

    # Rempli par le moteur après analyse
    gpx_start_time: Optional[datetime] = None   # timestamp GPX aligné
    gpx_offset_s: float = 0.0                   # décalage calculé (GPX time − video creation_time)
    sync_confidence: float = 0.0                # 0..1 confiance de la sync
    sync_method: str = "none"                   # "timestamp" | "manual" | "motion_correlation"

    @property
    def total_frames(self) -> int:
        return int(self.duration_s * self.fps)


@dataclass
class FrameData:
    """Données GPX interpolées pour une frame précise d'une vidéo."""
    frame_index: int
    video_time_s: float          # temps dans la vidéo (0 → duration)
    gpx_time: datetime           # timestamp GPX correspondant

    # Métriques (None si pas de données GPX disponibles à ce moment)
    speed_kmh: Optional[float] = None
    speed_ms: Optional[float] = None
    pace_s_per_km: Optional[float] = None
    slope_pct: Optional[float] = None
    elevation_m: Optional[float] = None
    distance_m: Optional[float] = None      # distance cumulée depuis départ GPX
    heart_rate: Optional[int] = None
    cadence: Optional[int] = None
    power: Optional[int] = None
    temperature: Optional[float] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    bearing: Optional[float] = None

    # Dérivées calculées
    elevation_gain_so_far: float = 0.0      # D+ cumulé jusqu'à cette frame
    distance_from_start_km: float = 0.0


@dataclass
class VideoSyncResult:
    """Résultat de sync pour un clip vidéo."""
    video: VideoMeta
    frame_data: list[FrameData]             # une entrée par frame
    coverage_pct: float                     # % du clip couvert par des données GPX (0..100)
    gpx_segment_start: Optional[datetime] = None
    gpx_segment_end: Optional[datetime] = None
    has_data: bool = False


@dataclass
class SessionSyncResult:
    """Résultat global de la session (tous clips + GPX)."""
    gpx_start: datetime
    gpx_end: datetime
    videos: list[VideoSyncResult]
    global_offset_s: float = 0.0           # offset manuel appliqué à toute la session
    warnings: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Extraction métadonnées vidéo via ffprobe
# ─────────────────────────────────────────────────────────────────────────────

def probe_video(local_path: str) -> VideoMeta:
    """
    Extrait les métadonnées complètes d'une vidéo locale via ffprobe.
    C'est la seule interaction avec le fichier physique.

    Retourne un VideoMeta avec creation_time si présent dans les tags.
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        local_path
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe error: {result.stderr}")

        info = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffprobe timeout — fichier peut-être corrompu")
    except json.JSONDecodeError:
        raise RuntimeError("ffprobe output invalide")

    fmt = info.get("format", {})
    streams = info.get("streams", [])

    # Durée
    duration_s = float(fmt.get("duration", 0))

    # FPS depuis le stream vidéo
    fps = 30.0
    width, height = 1920, 1080
    codec = "unknown"

    for stream in streams:
        if stream.get("codec_type") == "video":
            codec = stream.get("codec_name", "unknown")
            width = stream.get("width", 1920)
            height = stream.get("height", 1080)

            # FPS : avg_frame_rate ou r_frame_rate
            for fps_key in ("avg_frame_rate", "r_frame_rate"):
                fps_raw = stream.get(fps_key, "")
                if fps_raw and fps_raw != "0/0":
                    try:
                        num, den = fps_raw.split("/")
                        fps = float(num) / float(den)
                        break
                    except (ValueError, ZeroDivisionError):
                        pass
            break

    # ── Stratégie de lecture de la date d'enregistrement ──────────────────
    #
    # Les fichiers iPhone ont DEUX champs de date :
    #
    # 1. com.apple.quicktime.creationdate → DATE DE DÉBUT réelle (avec TZ locale)
    #    Exemple : "2025-12-04T07:40:56-0500"
    #    ✅ C'est celle-ci qu'on veut — ne PAS soustraire la durée
    #
    # 2. creation_time (format QuickTime standard) → peut être la date d'export
    #    iCloud/Photos ou la date de fin selon le logiciel.
    #    ⚠️  Souvent écrasée lors d'un export depuis Photos macOS → inutilisable
    #
    # Priorité : com.apple.quicktime.creationdate > creation_time stream > creation_time format

    creation_time = None
    used_quicktime_native = False

    def parse_dt(raw: str) -> Optional[datetime]:
        """Parse une date ISO 8601 avec ou sans timezone."""
        if not raw:
            return None
        try:
            raw = raw.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            try:
                return datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                return None

    all_tags = dict(fmt.get("tags", {}))
    for stream in streams:
        all_tags.update(stream.get("tags", {}))

    # 1. Priorité absolue : com.apple.quicktime.creationdate (= heure de début réelle)
    qt_date = all_tags.get("com.apple.quicktime.creationdate")
    timezone_offset_h = 0.0

    if qt_date:
        # Extrait l'offset TZ local depuis le tag (ex: "2025-12-04T08:09:30-0500" → -5.0)
        try:
            import re
            tz_match = re.search(r'([+-])(\d{2}):?(\d{2})$', qt_date.strip())
            if tz_match:
                sign = 1 if tz_match.group(1) == '+' else -1
                timezone_offset_h = sign * (int(tz_match.group(2)) + int(tz_match.group(3)) / 60)
        except Exception:
            pass

        creation_time = parse_dt(qt_date)
        if creation_time:
            used_quicktime_native = True  # C'est le début → pas de correction durée

    # 2. Fallback : creation_time standard (= heure de FIN sur iPhone natif)
    if creation_time is None:
        raw_ct = all_tags.get("creation_time")
        if raw_ct:
            creation_time = parse_dt(raw_ct)
            # Corrige : iPhone stocke l'heure de fin dans ce champ
            if creation_time is not None:
                creation_time = creation_time - timedelta(seconds=duration_s)

    return VideoMeta(
        filename=local_path.split("/")[-1],
        local_path=local_path,
        duration_s=duration_s,
        fps=fps,
        width=width,
        height=height,
        creation_time=creation_time,
        timezone_offset_h=timezone_offset_h,
        codec=codec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Interpolation GPX
# ─────────────────────────────────────────────────────────────────────────────

def _interpolate(v1, v2, t: float):
    """Interpolation linéaire entre deux valeurs (t ∈ [0,1])."""
    if v1 is None and v2 is None:
        return None
    if v1 is None:
        return v2
    if v2 is None:
        return v1
    return v1 + (v2 - v1) * t


def _interpolate_int(v1, v2, t: float) -> Optional[int]:
    r = _interpolate(v1, v2, t)
    return round(r) if r is not None else None


def _angle_interpolate(a1: float, a2: float, t: float) -> float:
    """Interpolation angulaire (cap/bearing) sans sauts 359°→0°."""
    diff = ((a2 - a1 + 180) % 360) - 180
    return (a1 + diff * t) % 360


def interpolate_gpx_at_time(
    points: list[TrackPoint],
    target_time: datetime,
) -> Optional[FrameData]:
    """
    Interpole toutes les métriques GPX au timestamp exact demandé.
    Retourne None si hors de la plage GPX.
    """
    if not points:
        return None

    # Vérifie les bornes
    if target_time < points[0].time or target_time > points[-1].time:
        return None

    # Recherche dichotomique du segment
    lo, hi = 0, len(points) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if points[mid].time <= target_time:
            lo = mid
        else:
            hi = mid

    p1, p2 = points[lo], points[hi]
    span = (p2.time - p1.time).total_seconds()

    if span <= 0:
        t = 0.0
    else:
        t = (target_time - p1.time).total_seconds() / span

    # Clamp t
    t = max(0.0, min(1.0, t))

    return FrameData(
        frame_index=-1,          # sera rempli par l'appelant
        video_time_s=0.0,        # sera rempli par l'appelant
        gpx_time=target_time,
        speed_kmh=_interpolate(p1.speed_kmh, p2.speed_kmh, t),
        speed_ms=_interpolate(p1.speed_ms, p2.speed_ms, t),
        pace_s_per_km=_interpolate(p1.pace_s_per_km, p2.pace_s_per_km, t),
        slope_pct=_interpolate(p1.slope_pct, p2.slope_pct, t),
        elevation_m=_interpolate(p1.elevation, p2.elevation, t),
        distance_m=_interpolate(p1.distance_m, p2.distance_m, t),
        heart_rate=_interpolate_int(p1.heart_rate, p2.heart_rate, t),
        cadence=_interpolate_int(p1.cadence, p2.cadence, t),
        power=_interpolate_int(p1.power, p2.power, t),
        temperature=_interpolate(p1.temperature, p2.temperature, t),
        lat=_interpolate(p1.lat, p2.lat, t),
        lon=_interpolate(p1.lon, p2.lon, t),
        bearing=_angle_interpolate(p1.bearing, p2.bearing, t),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Moteur de Synchronisation Principal
# ─────────────────────────────────────────────────────────────────────────────

class SyncEngine:
    """
    Moteur de synchronisation GPX ↔ Vidéos.

    Stratégie de sync (par ordre de priorité) :
      1. TIMESTAMP : création_time de la vidéo (méta EXIF iPhone)
      2. MANUEL   : offset fourni par l'utilisateur (via slider dans l'app)
      3. CORRÉLATION : comparaison mouvement vidéo ↔ vitesse GPX (futur)
    """

    def __init__(
        self,
        gpx_points: list[TrackPoint],
        global_offset_s: float = 0.0,
    ):
        """
        gpx_points    : résultat de gpx_parser.parse_gpx()
        global_offset_s : décalage global à ajouter à toutes les créations_time
                          (positif = vidéo démarre plus tôt dans le GPX)
        """
        if not gpx_points:
            raise ValueError("La liste de points GPX est vide.")

        self.points = gpx_points
        self.global_offset_s = global_offset_s
        self.gpx_start = gpx_points[0].time
        self.gpx_end = gpx_points[-1].time

    # ── Méthode principale ────────────────────────────────────────────────

    def sync_video(
        self,
        video: VideoMeta,
        manual_offset_s: float = 0.0,
    ) -> VideoSyncResult:
        """
        Synchronise UN clip vidéo sur la timeline GPX.

        manual_offset_s : ajustement manuel de l'utilisateur (secondes)
                          positif = décale la vidéo vers l'avant dans le GPX

        Retourne un VideoSyncResult avec frame_data pour chaque frame.
        """
        warnings = []

        # ── Étape 1 : Déterminer l'heure de début GPX de la vidéo ──────
        gpx_video_start = self._resolve_gpx_start(video, manual_offset_s, warnings)

        if gpx_video_start is None:
            return VideoSyncResult(
                video=video,
                frame_data=[],
                coverage_pct=0.0,
                has_data=False,
            )

        video.gpx_start_time = gpx_video_start

        # ── Étape 2 : Générer frame_data pour chaque frame ──────────────
        frame_data = self._generate_frame_data(video, gpx_video_start)

        # ── Étape 3 : Calcul de la couverture ───────────────────────────
        covered = sum(1 for fd in frame_data if fd.speed_kmh is not None)
        coverage_pct = (covered / len(frame_data) * 100) if frame_data else 0.0

        gpx_end_time = gpx_video_start + timedelta(seconds=video.duration_s)

        return VideoSyncResult(
            video=video,
            frame_data=frame_data,
            coverage_pct=round(coverage_pct, 1),
            gpx_segment_start=gpx_video_start,
            gpx_segment_end=gpx_end_time,
            has_data=coverage_pct > 5.0,
        )

    def sync_all(
        self,
        videos: list[VideoMeta],
        manual_offsets: Optional[dict[str, float]] = None,
    ) -> SessionSyncResult:
        """
        Synchronise tous les clips en une fois.

        manual_offsets : dict { filename → offset_s } pour ajustements fins par clip
        """
        manual_offsets = manual_offsets or {}
        results = []
        warnings = []

        for video in videos:
            offset = manual_offsets.get(video.filename, 0.0)
            result = self.sync_video(video, manual_offset_s=offset)
            results.append(result)

            if not result.has_data:
                warnings.append(
                    f"⚠️  {video.filename} : aucune donnée GPX couverte. "
                    f"Vérifiez l'heure de création ({video.creation_time})."
                )

        return SessionSyncResult(
            gpx_start=self.gpx_start,
            gpx_end=self.gpx_end,
            videos=results,
            global_offset_s=self.global_offset_s,
            warnings=warnings,
        )

    # ── Méthodes internes ─────────────────────────────────────────────────

    def _resolve_gpx_start(
        self,
        video: VideoMeta,
        manual_offset_s: float,
        warnings: list,
    ) -> Optional[datetime]:
        """
        Calcule le timestamp GPX correspondant au début du clip.

        Logique :
          gpx_video_start = video.creation_time
                          + global_offset_s          (correction globale)
                          + manual_offset_s           (correction fine par clip)

        Si pas de creation_time → retourne None (sync impossible sans offset manuel).
        """
        if video.creation_time is None:
            if manual_offset_s == 0.0 and self.global_offset_s == 0.0:
                warnings.append(
                    f"{video.filename} : pas de creation_time dans les métadonnées. "
                    f"Un offset manuel est requis."
                )
                return None
            # Si offset manuel fourni, on l'applique depuis le début du GPX
            base = self.gpx_start
            total_offset = self.global_offset_s + manual_offset_s
            return base + timedelta(seconds=total_offset)

        # Heure de début vidéo + corrections
        total_offset_s = self.global_offset_s + manual_offset_s
        gpx_start = video.creation_time + timedelta(seconds=total_offset_s)

        # Vérification sanity : le clip doit avoir une intersection avec le GPX
        clip_end = gpx_start + timedelta(seconds=video.duration_s)

        if clip_end < self.gpx_start:
            warnings.append(
                f"{video.filename} : clip se termine avant le début GPX "
                f"({clip_end.isoformat()} < {self.gpx_start.isoformat()}). "
                f"Ajustez l'offset."
            )
        elif gpx_start > self.gpx_end:
            warnings.append(
                f"{video.filename} : clip commence après la fin GPX "
                f"({gpx_start.isoformat()} > {self.gpx_end.isoformat()}). "
                f"Ajustez l'offset."
            )
        else:
            video.sync_method = "timestamp"
            video.sync_confidence = self._estimate_confidence(gpx_start, clip_end)

        return gpx_start

    def _estimate_confidence(self, clip_start: datetime, clip_end: datetime) -> float:
        """
        Estime la confiance de la sync (0..1).
        Basé sur le chevauchement entre le clip et la plage GPX.
        """
        overlap_start = max(clip_start, self.gpx_start)
        overlap_end = min(clip_end, self.gpx_end)

        if overlap_end <= overlap_start:
            return 0.0

        clip_duration = (clip_end - clip_start).total_seconds()
        overlap = (overlap_end - overlap_start).total_seconds()

        return min(1.0, overlap / clip_duration)

    def _generate_frame_data(
        self,
        video: VideoMeta,
        gpx_video_start: datetime,
    ) -> list[FrameData]:
        """
        Génère les données GPX pour chaque frame de la vidéo.

        Optimisation : on n'interpole pas frame par frame (30fps = 30 calculs/sec)
        mais on utilise un pas adaptatif :
          - 1 calcul par frame si fps ≤ 30
          - 1 calcul toutes les N frames si fps > 30 (puis interpolation entre)

        Pour un rendu fluide des widgets, les données sont quand même stockées
        par frame.
        """
        frame_data = []
        step = max(1, int(video.fps / 30))  # ≥1, réduit calculs pour 60/120fps

        # Pré-calcul des points clés
        keyframes: dict[int, FrameData] = {}

        for frame_idx in range(0, video.total_frames, step):
            video_time_s = frame_idx / video.fps
            target_time = gpx_video_start + timedelta(seconds=video_time_s)

            fd = interpolate_gpx_at_time(self.points, target_time)

            if fd is not None:
                fd.frame_index = frame_idx
                fd.video_time_s = video_time_s
                # Calcul distance depuis début de l'activité (pas du clip)
                if fd.distance_m is not None:
                    fd.distance_from_start_km = fd.distance_m / 1000.0
            else:
                # Hors plage GPX : frame vide mais référencée
                fd = FrameData(
                    frame_index=frame_idx,
                    video_time_s=video_time_s,
                    gpx_time=target_time,
                )

            keyframes[frame_idx] = fd

        # Expansion : une entrée par frame (interpolation linéaire entre keyframes)
        key_indices = sorted(keyframes.keys())

        for i, ki in enumerate(key_indices):
            kfd = keyframes[ki]
            next_ki = key_indices[i + 1] if i + 1 < len(key_indices) else None
            next_kfd = keyframes[next_ki] if next_ki else None

            frames_in_segment = step if next_ki else 1

            for local_idx in range(frames_in_segment):
                actual_frame = ki + local_idx
                if actual_frame >= video.total_frames:
                    break

                if local_idx == 0 or next_kfd is None:
                    fd = kfd
                else:
                    # Interpolation légère entre deux keyframes GPX
                    t = local_idx / frames_in_segment
                    fd = FrameData(
                        frame_index=actual_frame,
                        video_time_s=actual_frame / video.fps,
                        gpx_time=kfd.gpx_time + timedelta(seconds=local_idx / video.fps),
                        speed_kmh=_interpolate(kfd.speed_kmh, next_kfd.speed_kmh, t),
                        speed_ms=_interpolate(kfd.speed_ms, next_kfd.speed_ms, t),
                        pace_s_per_km=_interpolate(kfd.pace_s_per_km, next_kfd.pace_s_per_km, t),
                        slope_pct=_interpolate(kfd.slope_pct, next_kfd.slope_pct, t),
                        elevation_m=_interpolate(kfd.elevation_m, next_kfd.elevation_m, t),
                        distance_m=_interpolate(kfd.distance_m, next_kfd.distance_m, t),
                        heart_rate=_interpolate_int(kfd.heart_rate, next_kfd.heart_rate, t),
                        cadence=_interpolate_int(kfd.cadence, next_kfd.cadence, t),
                        power=_interpolate_int(kfd.power, next_kfd.power, t),
                        temperature=_interpolate(kfd.temperature, next_kfd.temperature, t),
                        lat=_interpolate(kfd.lat, next_kfd.lat, t),
                        lon=_interpolate(kfd.lon, next_kfd.lon, t),
                        bearing=_angle_interpolate(
                            kfd.bearing or 0, next_kfd.bearing or 0, t
                        ),
                        distance_from_start_km=_interpolate(
                            kfd.distance_from_start_km,
                            next_kfd.distance_from_start_km,
                            t,
                        ) or 0.0,
                    )

                frame_data.append(fd)

        return frame_data

    # ── Utilitaires publics ───────────────────────────────────────────────

    def get_sync_report(self, session: SessionSyncResult) -> dict:
        """Rapport JSON complet de la session de sync (pour le frontend)."""
        videos_report = []
        for vr in session.videos:
            videos_report.append({
                "filename": vr.video.filename,
                "duration_s": vr.video.duration_s,
                "fps": vr.video.fps,
                "resolution": f"{vr.video.width}x{vr.video.height}",
                "creation_time": vr.video.creation_time.isoformat() if vr.video.creation_time else None,
                "gpx_segment_start": vr.gpx_segment_start.isoformat() if vr.gpx_segment_start else None,
                "gpx_segment_end": vr.gpx_segment_end.isoformat() if vr.gpx_segment_end else None,
                "coverage_pct": vr.coverage_pct,
                "has_data": vr.has_data,
                "sync_method": vr.video.sync_method,
                "sync_confidence": round(vr.video.sync_confidence, 2),
                "frame_count": len(vr.frame_data),
            })

        return {
            "gpx_start": session.gpx_start.isoformat(),
            "gpx_end": session.gpx_end.isoformat(),
            "gpx_duration_s": (session.gpx_end - session.gpx_start).total_seconds(),
            "global_offset_s": session.global_offset_s,
            "video_count": len(session.videos),
            "videos": videos_report,
            "warnings": session.warnings,
        }
