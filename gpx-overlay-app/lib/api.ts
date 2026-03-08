/**
 * Client API — GPX Overlay Backend
 * Toutes les calls vers le serveur FastAPI Python.
 *
 * En dev local : le serveur tourne sur http://localhost:8000
 * En prod : remplacer par l'URL déployée
 */

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { uploadAsync, FileSystemUploadType } = require('expo-file-system/legacy');

const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

// ─── GPX ──────────────────────────────────────────────────────────────────────

export async function uploadGpxFile(uri: string, _filename: string) {
  const result = await uploadAsync(`${BASE_URL}/gpx/upload`, uri, {
    httpMethod: 'POST',
    uploadType: FileSystemUploadType.MULTIPART,
    fieldName: 'file',
    mimeType: 'application/gpx+xml',
    parameters: {},
  });
  if (result.status < 200 || result.status >= 300) {
    throw new Error(`Upload GPX ${result.status}: ${result.body}`);
  }
  return JSON.parse(result.body);
}

// ─── Vidéos metadata ──────────────────────────────────────────────────────────

export async function submitVideoMetadata(payload: {
  session_id: string;
  global_offset_s: number;
  videos: {
    filename: string;
    duration_s: number;
    fps: number;
    width: number;
    height: number;
    creation_time?: string;
    timezone_offset_h?: number;
    codec?: string;
  }[];
}) {
  return request('/videos/metadata', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

// ─── Sync ─────────────────────────────────────────────────────────────────────

export async function getFrameData(
  sessionId: string,
  filename: string,
  offsetS = 0,
  sampleRate = 1
) {
  const params = new URLSearchParams({
    offset_s: String(offsetS),
    sample_rate: String(sampleRate),
  });
  return request(`/sync/frame-data/${sessionId}/${encodeURIComponent(filename)}?${params}`);
}

export async function getPreviewFrameData(
  sessionId: string,
  filename: string,
  offsetS = 0
) {
  return request(
    `/sync/preview/${sessionId}/${encodeURIComponent(filename)}?offset_s=${offsetS}`
  );
}

export async function adjustOffset(sessionId: string, filename: string, offsetS: number) {
  const params = new URLSearchParams({
    session_id: sessionId,
    filename,
    offset_s: String(offsetS),
  });
  return request(`/sync/adjust-offset?${params}`, { method: 'POST' });
}

export async function calibrateOffset(payload: {
  session_id: string;
  filename: string;
  video_time_s: number;
  known_distance_m: number;
}) {
  const params = new URLSearchParams({
    session_id: payload.session_id,
    filename: payload.filename,
    video_time_s: String(payload.video_time_s),
    known_distance_m: String(payload.known_distance_m),
  });
  return request(`/sync/calibrate?${params}`, { method: 'POST' });
}

// ─── Widgets ──────────────────────────────────────────────────────────────────

export async function getWidgets() {
  return request('/widgets');
}
