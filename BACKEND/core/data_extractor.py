"""
Data Extractor — Formatage des métriques pour les widgets
==========================================================
Prend les FrameData brutes du SyncEngine et les formate
en valeurs prêtes à afficher pour chaque type de widget.

Principe : le renderer ne sait PAS ce que signifie une valeur.
Il sait juste dessiner un widget avec { label, value, unit, color }.
C'est ce module qui fait la conversion + logique métier.
"""

from dataclasses import dataclass
from typing import Optional
import math

from .sync_engine import FrameData


# ─────────────────────────────────────────────────────────────────────────────
# Valeur formatée pour un widget
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WidgetValue:
    """Valeur prête à être dessinée par le renderer."""
    key: str                        # identifiant du widget (ex: "speed", "hr")
    label: str                      # libellé affiché (ex: "VITESSE")
    value: str                      # valeur formatée (ex: "12.4")
    unit: str                       # unité (ex: "km/h")
    raw: Optional[float] = None     # valeur brute pour calculs (graphiques, etc.)
    available: bool = True          # False si donnée absente pour ce frame
    color_hint: Optional[str] = None  # couleur suggérée selon intensité (ex: "#FF4444")


# ─────────────────────────────────────────────────────────────────────────────
# Formateurs par type de widget
# ─────────────────────────────────────────────────────────────────────────────

def fmt_speed(fd: FrameData) -> WidgetValue:
    """Vitesse en km/h."""
    if fd.speed_kmh is None or fd.speed_kmh < 0:
        return WidgetValue("speed", "VITESSE", "--", "km/h", available=False)
    v = round(fd.speed_kmh, 1)
    return WidgetValue("speed", "VITESSE", f"{v:.1f}", "km/h", raw=v)


def fmt_speed_ms(fd: FrameData) -> WidgetValue:
    """Vitesse en m/s."""
    if fd.speed_ms is None:
        return WidgetValue("speed_ms", "VITESSE", "--", "m/s", available=False)
    v = round(fd.speed_ms, 2)
    return WidgetValue("speed_ms", "VITESSE", f"{v:.2f}", "m/s", raw=v)


def fmt_pace(fd: FrameData) -> WidgetValue:
    """
    Allure en min/km (ex: 5'23").
    Gère les allures absurdes (< 1min/km ou > 30min/km).
    """
    if fd.pace_s_per_km is None or fd.pace_s_per_km <= 0:
        return WidgetValue("pace", "ALLURE", "--", "/km", available=False)

    s = fd.pace_s_per_km
    if s < 60 or s > 1800:  # < 1min/km ou > 30min/km → probablement arrêté
        return WidgetValue("pace", "ALLURE", "--", "/km", available=False)

    minutes = int(s // 60)
    seconds = int(s % 60)
    value_str = f"{minutes}'{seconds:02d}\""

    return WidgetValue("pace", "ALLURE", value_str, "/km", raw=s)


def fmt_heart_rate(fd: FrameData) -> WidgetValue:
    """Fréquence cardiaque avec indication de zone."""
    if fd.heart_rate is None:
        return WidgetValue("hr", "CŒUR", "--", "bpm", available=False)

    hr = fd.heart_rate
    color = _hr_color(hr)
    return WidgetValue("hr", "CŒUR", str(hr), "bpm", raw=float(hr), color_hint=color)


def fmt_slope(fd: FrameData) -> WidgetValue:
    """
    Pente en % avec signe et direction.
    Ex: "+8.2%" (montée) ou "-3.1%" (descente)
    """
    if fd.slope_pct is None:
        return WidgetValue("slope", "PENTE", "--", "%", available=False)

    s = fd.slope_pct
    # Lissage : ignore les micro-variations GPS
    if abs(s) < 0.3:
        s = 0.0

    sign = "+" if s > 0 else ""
    color = _slope_color(s)
    return WidgetValue("slope", "PENTE", f"{sign}{s:.1f}", "%", raw=s, color_hint=color)


def fmt_elevation(fd: FrameData) -> WidgetValue:
    """Altitude en mètres."""
    if fd.elevation_m is None:
        return WidgetValue("elevation", "ALTITUDE", "--", "m", available=False)
    v = round(fd.elevation_m, 0)
    return WidgetValue("elevation", "ALTITUDE", f"{int(v)}", "m", raw=v)


def fmt_distance(fd: FrameData) -> WidgetValue:
    """
    Distance depuis le départ.
    < 1km → affiche en m, sinon en km.
    """
    if fd.distance_m is None:
        return WidgetValue("distance", "DISTANCE", "--", "km", available=False)

    d = fd.distance_m
    if d < 1000:
        return WidgetValue("distance", "DISTANCE", f"{int(d)}", "m", raw=d)
    else:
        km = d / 1000
        return WidgetValue("distance", "DISTANCE", f"{km:.2f}", "km", raw=km)


def fmt_cadence(fd: FrameData) -> WidgetValue:
    """
    Cadence en pas/min (running) ou rpm (cycling).
    Le frontend décide du label selon le type d'activité.
    """
    if fd.cadence is None:
        return WidgetValue("cadence", "CADENCE", "--", "spm", available=False)
    return WidgetValue("cadence", "CADENCE", str(fd.cadence), "spm", raw=float(fd.cadence))


def fmt_power(fd: FrameData) -> WidgetValue:
    """Puissance en watts."""
    if fd.power is None:
        return WidgetValue("power", "PUISSANCE", "--", "W", available=False)
    color = _power_color(fd.power)
    return WidgetValue("power", "PUISSANCE", str(fd.power), "W", raw=float(fd.power), color_hint=color)


def fmt_temperature(fd: FrameData) -> WidgetValue:
    """Température extérieure."""
    if fd.temperature is None:
        return WidgetValue("temperature", "TEMP.", "--", "°C", available=False)
    return WidgetValue("temperature", "TEMP.", f"{fd.temperature:.1f}", "°C", raw=fd.temperature)


def fmt_coordinates(fd: FrameData) -> WidgetValue:
    """Coordonnées GPS formatées."""
    if fd.lat is None or fd.lon is None:
        return WidgetValue("coords", "GPS", "--", "", available=False)
    value = f"{fd.lat:.5f}, {fd.lon:.5f}"
    return WidgetValue("coords", "GPS", value, "")


def fmt_bearing(fd: FrameData) -> WidgetValue:
    """Cap cardinal (N, NE, E, SE, S, SO, O, NO)."""
    if fd.bearing is None:
        return WidgetValue("bearing", "CAP", "--", "", available=False)
    cardinal = _bearing_to_cardinal(fd.bearing)
    return WidgetValue("bearing", "CAP", cardinal, "", raw=fd.bearing)


def fmt_elevation_gain(fd: FrameData) -> WidgetValue:
    """Dénivelé positif cumulé."""
    v = fd.elevation_gain_so_far
    return WidgetValue("elev_gain", "D+", f"{int(v)}", "m", raw=v)


def fmt_time_elapsed(fd: FrameData, gpx_start) -> WidgetValue:
    """Temps écoulé depuis le début de l'activité."""
    elapsed_s = (fd.gpx_time - gpx_start).total_seconds()
    if elapsed_s < 0:
        elapsed_s = 0
    h = int(elapsed_s // 3600)
    m = int((elapsed_s % 3600) // 60)
    s = int(elapsed_s % 60)
    if h > 0:
        value = f"{h}:{m:02d}:{s:02d}"
    else:
        value = f"{m}:{s:02d}"
    return WidgetValue("time_elapsed", "TEMPS", value, "", raw=elapsed_s)


# ─────────────────────────────────────────────────────────────────────────────
# Extracteur principal
# ─────────────────────────────────────────────────────────────────────────────

# Registre de tous les widgets disponibles
WIDGET_REGISTRY = {
    "speed":        fmt_speed,
    "speed_ms":     fmt_speed_ms,
    "pace":         fmt_pace,
    "hr":           fmt_heart_rate,
    "slope":        fmt_slope,
    "elevation":    fmt_elevation,
    "distance":     fmt_distance,
    "cadence":      fmt_cadence,
    "power":        fmt_power,
    "temperature":  fmt_temperature,
    "coords":       fmt_coordinates,
    "bearing":      fmt_bearing,
    "elev_gain":    fmt_elevation_gain,
    # "time_elapsed" nécessite gpx_start → géré séparément
}


def extract_widget_values(
    fd: FrameData,
    requested_widgets: list[str],
    gpx_start=None,
) -> dict[str, WidgetValue]:
    """
    Extrait et formate les valeurs pour une liste de widgets demandés.

    requested_widgets : liste de clés (ex: ["speed", "hr", "slope"])
    Retourne un dict { widget_key → WidgetValue }
    """
    result = {}

    for key in requested_widgets:
        if key == "time_elapsed":
            if gpx_start is not None:
                result[key] = fmt_time_elapsed(fd, gpx_start)
            continue

        formatter = WIDGET_REGISTRY.get(key)
        if formatter:
            result[key] = formatter(fd)

    return result


def get_available_widgets() -> list[dict]:
    """
    Retourne la liste de tous les widgets disponibles avec leurs métadonnées.
    Utilisé par le frontend pour construire le sélecteur de widgets.
    """
    widgets = [
        {"key": "speed",        "label": "Vitesse",             "unit": "km/h",  "category": "movement"},
        {"key": "pace",         "label": "Allure",              "unit": "/km",   "category": "movement"},
        {"key": "hr",           "label": "Fréquence Cardiaque", "unit": "bpm",   "category": "biometric"},
        {"key": "slope",        "label": "Pente",               "unit": "%",     "category": "terrain"},
        {"key": "elevation",    "label": "Altitude",            "unit": "m",     "category": "terrain"},
        {"key": "elev_gain",    "label": "Dénivelé +",          "unit": "m",     "category": "terrain"},
        {"key": "distance",     "label": "Distance",            "unit": "km",    "category": "movement"},
        {"key": "cadence",      "label": "Cadence",             "unit": "spm",   "category": "biometric"},
        {"key": "power",        "label": "Puissance",           "unit": "W",     "category": "biometric"},
        {"key": "temperature",  "label": "Température",         "unit": "°C",    "category": "environment"},
        {"key": "bearing",      "label": "Cap",                 "unit": "",      "category": "navigation"},
        {"key": "coords",       "label": "Coordonnées GPS",     "unit": "",      "category": "navigation"},
        {"key": "time_elapsed", "label": "Temps",               "unit": "",      "category": "movement"},
        {"key": "speed_ms",     "label": "Vitesse (m/s)",       "unit": "m/s",   "category": "movement"},
    ]
    return widgets


# ─────────────────────────────────────────────────────────────────────────────
# Helpers couleur / logique métier
# ─────────────────────────────────────────────────────────────────────────────

def _hr_color(hr: int) -> str:
    """Zone FC → couleur hex."""
    if hr < 100:   return "#FFFFFF"
    if hr < 130:   return "#00C8FF"   # zone 1 — récup
    if hr < 150:   return "#00FF88"   # zone 2 — endurance
    if hr < 165:   return "#FFD700"   # zone 3 — tempo
    if hr < 180:   return "#FF8C00"   # zone 4 — seuil
    return "#FF2244"                   # zone 5 — max


def _slope_color(slope: float) -> str:
    """Pente → couleur indicative."""
    if slope > 15:  return "#FF2244"
    if slope > 8:   return "#FF8C00"
    if slope > 3:   return "#FFD700"
    if slope < -8:  return "#00C8FF"
    if slope < -3:  return "#88CCFF"
    return "#FFFFFF"


def _power_color(watts: int) -> str:
    """Puissance → couleur (zones FTP approximatives)."""
    if watts < 100:  return "#AAAAAA"
    if watts < 200:  return "#00C8FF"
    if watts < 280:  return "#00FF88"
    if watts < 350:  return "#FFD700"
    if watts < 420:  return "#FF8C00"
    return "#FF2244"


def _bearing_to_cardinal(degrees: float) -> str:
    """Degrés → direction cardinale."""
    cardinals = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    idx = round(degrees / 45) % 8
    return cardinals[idx]


# ─────────────────────────────────────────────────────────────────────────────
# Calcul D+ cumulé (post-processing sur toute la session)
# ─────────────────────────────────────────────────────────────────────────────

def compute_elevation_gain_series(frame_data: list[FrameData]) -> list[FrameData]:
    """
    Calcule et injecte elevation_gain_so_far dans chaque FrameData.
    À appeler une fois après sync, avant le rendu.
    """
    gain = 0.0
    prev_elev = None

    for fd in frame_data:
        if fd.elevation_m is not None:
            if prev_elev is not None and fd.elevation_m > prev_elev:
                gain += fd.elevation_m - prev_elev
            prev_elev = fd.elevation_m
        fd.elevation_gain_so_far = gain

    return frame_data
