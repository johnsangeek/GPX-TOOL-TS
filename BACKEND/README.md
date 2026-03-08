# GPX Overlay — Backend (Option B : rendu sur iPhone)

## Principe fondateur

**Le serveur ne touche JAMAIS aux vidéos.**

```
iPhone                          Serveur FastAPI
──────                          ───────────────
Vidéos restent locales          Parse GPX
Lit les métadonnées vidéo  →    Calcule la sync
                           ←    Retourne JSON frames (quelques Ko)
Rend la vidéo (AVFoundation)
Exporte le résultat             Génère les images stats (seul rendu)
```

## Démarrage

```bash
cd BACKEND
pip install -r requirements.txt
brew install ffmpeg   # pour les stats images uniquement
uvicorn main:app --reload --port 8000
# → http://localhost:8000/docs
```

## Flow API complet

```
1. POST  /api/v1/gpx/upload
         body: fichier .gpx
         → { session_id, activity_summary, available_widgets }

2. POST  /api/v1/videos/metadata
         body: { session_id, videos: [{ filename, duration_s, fps,
                 width, height, creation_time, codec }] }
         → { videos: [{ coverage_pct, sync_confidence, gpx_segment... }] }

3. GET   /api/v1/sync/preview/{session_id}/{filename}?offset_s=0
         → JSON allégé (1 fps) pour preview temps réel dans l'app

4. POST  /api/v1/sync/adjust-offset
         body: { session_id, filename, offset_s }
         → { coverage_pct, sync_confidence }  ← l'utilisateur affine

5. GET   /api/v1/sync/frame-data/{session_id}/{filename}?offset_s=0
         → JSON complet (30fps) pour le rendu final sur iPhone

6. POST  /api/v1/render/stats-image   ← optionnel
         → JPEG image stats style Strava
```

## Format du JSON frame-data

Clés courtes pour minimiser la taille du JSON :

```json
{
  "fps": 30.0,
  "duration_s": 312.5,
  "coverage_pct": 98.2,
  "frames": [
    { "t": 0.000, "sp": 12.4, "pa": 290.3, "hr": 156, "sl": 2.1, "el": 245.0, "di": 1234.5 },
    { "t": 0.033, "sp": 12.5, "pa": 289.0, "hr": 157, "sl": 2.2, "el": 245.1, "di": 1234.9 },
    ...
  ],
  "keys": {
    "t": "video_time_s",   "sp": "speed_kmh",     "pa": "pace_s_per_km",
    "hr": "heart_rate_bpm","sl": "slope_pct",      "el": "elevation_m",
    "di": "distance_m",    "ca": "cadence_spm",    "pw": "power_w",
    "te": "temperature_c", "la": "latitude",       "lo": "longitude",
    "be": "bearing_deg",   "dg": "elev_gain_m"
  }
}
```

**Taille typique** : 1h de course à 30fps = ~100 000 frames → ~3-5 MB JSON

## Ce que fait l'iPhone avec ce JSON

```swift
// Côté iOS — AVFoundation
let composition = AVMutableVideoComposition()
composition.animationTool = AVVideoCompositionCoreAnimationTool(...)

// Pour chaque frame, lit frameData[frameIndex]
// et dessine les widgets avec Core Animation / Metal
```

## Widgets disponibles

| Clé           | Label              | Unité |
|---------------|--------------------|-------|
| `speed`       | Vitesse            | km/h  |
| `pace`        | Allure             | /km   |
| `hr`          | Fréquence Cardiaque| bpm   |
| `slope`       | Pente              | %     |
| `elevation`   | Altitude           | m     |
| `elev_gain`   | Dénivelé +         | m     |
| `distance`    | Distance           | km    |
| `cadence`     | Cadence            | spm   |
| `power`       | Puissance          | W     |
| `temperature` | Température        | °C    |
| `bearing`     | Cap                | N/NE… |
| `coords`      | GPS                | °     |
| `time_elapsed`| Temps              | mm:ss |
