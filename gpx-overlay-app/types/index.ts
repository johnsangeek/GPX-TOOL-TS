// ─── Projet ───────────────────────────────────────────────────────────────────

export interface Project {
  id: string;
  name: string;
  createdAt: string;
  // Step 1 — Source GPX
  sessionId?: string;
  gpxSource?: 'strava' | 'file';
  activityName?: string;
  activityDate?: string;
  activitySummary?: ActivitySummary;
  // Step 2 — Videos
  clips?: Clip[];
  // Step 3 — Sync
  syncResult?: SyncResult;
  calibrationMode?: 'auto' | 'manual' | 'clip';
  offsetS?: number;
  // Step 4 — Widgets
  widgetLayout?: WidgetPlacement[];
  // Step 5 — Export
  exportMode?: 'per_clip' | 'montage';
}

// ─── GPX / Activité ───────────────────────────────────────────────────────────

export interface ActivitySummary {
  start_time: string;
  end_time: string;
  duration_s: number;
  distance_km: number;
  elevation_gain_m: number;
  avg_speed_kmh: number;
  max_speed_kmh: number;
  avg_hr?: number;
  max_hr?: number;
  point_count: number;
}

// ─── Clips vidéo ──────────────────────────────────────────────────────────────

export interface Clip {
  filename: string;
  uri: string;               // chemin local sur l'iPhone
  duration_s: number;
  fps: number;
  width: number;
  height: number;
  creation_time?: string;    // ISO8601 depuis les métadonnées
  timezone_offset_h?: number;
  codec?: string;
  // Résultat de sync
  syncStatus?: 'ok' | 'warning' | 'error' | 'pending';
  coveragePct?: number;
  syncConfidence?: number;
  syncError?: string;
}

// ─── Sync ─────────────────────────────────────────────────────────────────────

export interface SyncResult {
  sessionId: string;
  gpxStart: string;
  gpxEnd: string;
  globalOffsetS: number;
  videos: VideoSyncInfo[];
  warnings: string[];
}

export interface VideoSyncInfo {
  filename: string;
  coverage_pct: number;
  sync_confidence: number;
  gpx_segment_start?: string;
  gpx_segment_end?: string;
  has_data: boolean;
  error?: string;
}

// ─── Widgets ──────────────────────────────────────────────────────────────────

export interface WidgetDef {
  key: string;
  label: string;
  unit: string;
  description: string;
  category: 'speed' | 'cardio' | 'terrain' | 'gps' | 'stats' | 'graph';
}

export interface WidgetPlacement {
  key: string;
  x: number;   // 0..1 relatif
  y: number;   // 0..1 relatif
}

// ─── Frame data ───────────────────────────────────────────────────────────────

export interface FrameData {
  t: number;
  sp?: number;   // speed km/h
  pa?: number;   // pace s/km
  hr?: number;   // heart rate bpm
  sl?: number;   // slope %
  el?: number;   // elevation m
  di?: number;   // distance m
  ca?: number;   // cadence spm
  pw?: number;   // power W
  te?: number;   // temperature °C
  la?: number;   // latitude
  lo?: number;   // longitude
  be?: number;   // bearing deg
  dg?: number;   // D+ cumulé m
}

export interface FrameDataResponse {
  filename: string;
  fps: number;
  duration_s: number;
  coverage_pct: number;
  sync_confidence: number;
  local_start_time?: string;
  timezone_offset_h?: number;
  elapsed_start_s: number;
  frame_count: number;
  frames: FrameData[];
}
