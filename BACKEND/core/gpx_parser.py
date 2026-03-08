"""
GPX Parser — Strava full extensions support
Extrait tous les trackpoints avec : lat, lon, elevation, time,
heart_rate, cadence, power, temperature, speed (calculée ou native)
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import math


# Namespaces GPX standards + extensions Strava/Garmin
NS = {
    "gpx":    "http://www.topografix.com/GPX/1/1",
    "gpxtpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
    "gpxdata":"http://www.cluetrust.com/XML/GPXDATA/1/0",
    "ns3":    "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
    "pwr":    "http://www.garmin.com/xmlschemas/PowerExtension/v1",
}


@dataclass
class TrackPoint:
    time: datetime
    lat: float
    lon: float
    elevation: Optional[float] = None
    heart_rate: Optional[int] = None
    cadence: Optional[int] = None        # pas/min (running) ou rpm (cycling)
    power: Optional[int] = None          # watts
    temperature: Optional[float] = None  # °C

    # Calculés lors du parsing
    speed_ms: float = 0.0                # m/s
    speed_kmh: float = 0.0              # km/h
    pace_s_per_km: float = 0.0          # secondes par km
    distance_m: float = 0.0             # distance cumulée depuis départ (m)
    slope_pct: float = 0.0              # pente en %
    bearing: float = 0.0                # cap en degrés


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance en mètres entre deux coordonnées GPS."""
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Cap en degrés entre deux points GPS."""
    dlon = math.radians(lon2 - lon1)
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _parse_time(s: str) -> datetime:
    """Parse ISO 8601 → datetime UTC aware."""
    s = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # fallback pour formats exotiques
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S+00:00")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _get_extension_value(trkpt: ET.Element, *tags: str) -> Optional[str]:
    """Cherche une valeur dans les extensions GPX (multi-namespace)."""
    extensions = trkpt.find("gpx:extensions", NS)
    if extensions is None:
        return None
    for tag in tags:
        # Cherche dans tous les sous-éléments récursivement
        for ns_prefix in NS:
            el = extensions.find(f".//{NS[ns_prefix]}{{{NS[ns_prefix]}}}{tag}")
            if el is not None and el.text:
                return el.text
        # Cherche sans namespace (certains exports Strava)
        el = extensions.find(f".//{tag}")
        if el is not None and el.text:
            return el.text
    return None


def _find_ext(trkpt: ET.Element, local_tag: str) -> Optional[str]:
    """
    Recherche agnostique au namespace pour une balise dans les extensions.
    Supporte : gpxtpx:hr, ns3:hr, hr, etc.
    """
    ext_block = trkpt.find("gpx:extensions", NS)
    if ext_block is None:
        # Essai sans namespace
        ext_block = trkpt.find("extensions")
    if ext_block is None:
        return None

    # Parcours de tous les descendants
    for el in ext_block.iter():
        # Récupère le local name (sans namespace)
        local = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if local == local_tag and el.text:
            return el.text.strip()
    return None


def parse_gpx(file_path: str) -> list[TrackPoint]:
    """
    Parse un fichier GPX et retourne la liste de TrackPoints enrichis.
    Calcule vitesse, allure, distance cumulée, pente, cap.
    """
    tree = ET.parse(file_path)
    root = tree.getroot()

    # Support GPX avec ou sans namespace
    ns_prefix = "{http://www.topografix.com/GPX/1/1}"
    if not root.tag.startswith("{"):
        ns_prefix = ""

    points: list[TrackPoint] = []

    for trkpt in root.iter(f"{ns_prefix}trkpt"):
        try:
            lat = float(trkpt.get("lat", 0))
            lon = float(trkpt.get("lon", 0))

            time_el = trkpt.find(f"{ns_prefix}time")
            if time_el is None or not time_el.text:
                continue
            time = _parse_time(time_el.text)

            ele_el = trkpt.find(f"{ns_prefix}ele")
            elevation = float(ele_el.text) if ele_el is not None and ele_el.text else None

            # Extensions
            hr_raw = _find_ext(trkpt, "hr") or _find_ext(trkpt, "heartRate")
            cad_raw = _find_ext(trkpt, "cad") or _find_ext(trkpt, "cadence")
            pwr_raw = _find_ext(trkpt, "power") or _find_ext(trkpt, "Watts")
            temp_raw = _find_ext(trkpt, "atemp") or _find_ext(trkpt, "temp")

            tp = TrackPoint(
                time=time,
                lat=lat,
                lon=lon,
                elevation=elevation,
                heart_rate=int(hr_raw) if hr_raw else None,
                cadence=int(cad_raw) if cad_raw else None,
                power=int(pwr_raw) if pwr_raw else None,
                temperature=float(temp_raw) if temp_raw else None,
            )
            points.append(tp)
        except (ValueError, TypeError, AttributeError):
            continue

    if not points:
        raise ValueError("Aucun trackpoint valide trouvé dans le fichier GPX.")

    # Trier par temps (sécurité)
    points.sort(key=lambda p: p.time)

    # ── Calculs inter-points ──────────────────────────────────────────────
    cumulative_distance = 0.0

    for i, pt in enumerate(points):
        if i == 0:
            pt.distance_m = 0.0
            pt.speed_ms = 0.0
            pt.speed_kmh = 0.0
            pt.pace_s_per_km = 0.0
            pt.slope_pct = 0.0
            pt.bearing = 0.0
            continue

        prev = points[i - 1]
        dt_sec = (pt.time - prev.time).total_seconds()
        if dt_sec <= 0:
            # Copie les valeurs précédentes si même timestamp
            pt.speed_ms = prev.speed_ms
            pt.speed_kmh = prev.speed_kmh
            pt.pace_s_per_km = prev.pace_s_per_km
            pt.slope_pct = prev.slope_pct
            pt.bearing = prev.bearing
            pt.distance_m = prev.distance_m
            continue

        dist_2d = _haversine(prev.lat, prev.lon, pt.lat, pt.lon)
        cumulative_distance += dist_2d
        pt.distance_m = cumulative_distance

        # Vitesse (lissage sur distance minimale 0.5m pour éviter artefacts GPS)
        if dist_2d > 0.5:
            pt.speed_ms = dist_2d / dt_sec
        else:
            pt.speed_ms = prev.speed_ms * 0.8  # légère décroissance si quasi immobile

        pt.speed_kmh = pt.speed_ms * 3.6

        # Allure (min/km)
        if pt.speed_ms > 0.1:
            pt.pace_s_per_km = 1000.0 / pt.speed_ms
        else:
            pt.pace_s_per_km = 0.0

        # Pente
        if dist_2d > 0.5 and pt.elevation is not None and prev.elevation is not None:
            dz = pt.elevation - prev.elevation
            pt.slope_pct = (dz / dist_2d) * 100.0
        else:
            pt.slope_pct = prev.slope_pct

        # Cap
        pt.bearing = _bearing(prev.lat, prev.lon, pt.lat, pt.lon)

    return points


def get_activity_summary(points: list[TrackPoint]) -> dict:
    """Résumé global de l'activité (pour le frontend)."""
    if not points:
        return {}

    total_time = (points[-1].time - points[0].time).total_seconds()
    total_dist = points[-1].distance_m

    elevations = [p.elevation for p in points if p.elevation is not None]
    elev_gain = 0.0
    elev_loss = 0.0
    for i in range(1, len(elevations)):
        diff = elevations[i] - elevations[i - 1]
        if diff > 0:
            elev_gain += diff
        else:
            elev_loss += abs(diff)

    speeds = [p.speed_kmh for p in points if p.speed_kmh > 0]
    hrs = [p.heart_rate for p in points if p.heart_rate]
    paces = [p.pace_s_per_km for p in points if p.pace_s_per_km > 0]

    return {
        "start_time": points[0].time.isoformat(),
        "end_time": points[-1].time.isoformat(),
        "duration_s": total_time,
        "distance_m": total_dist,
        "distance_km": total_dist / 1000,
        "avg_speed_kmh": sum(speeds) / len(speeds) if speeds else 0,
        "max_speed_kmh": max(speeds) if speeds else 0,
        "avg_pace_s_per_km": sum(paces) / len(paces) if paces else 0,
        "elevation_gain_m": elev_gain,
        "elevation_loss_m": elev_loss,
        "min_elevation_m": min(elevations) if elevations else None,
        "max_elevation_m": max(elevations) if elevations else None,
        "avg_hr": int(sum(hrs) / len(hrs)) if hrs else None,
        "max_hr": max(hrs) if hrs else None,
        "has_heart_rate": bool(hrs),
        "has_cadence": any(p.cadence for p in points),
        "has_power": any(p.power for p in points),
        "has_temperature": any(p.temperature for p in points),
        "point_count": len(points),
    }
