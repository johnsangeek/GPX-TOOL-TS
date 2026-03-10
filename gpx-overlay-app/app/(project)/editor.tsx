/**
 * editor.tsx — Éditeur principal style CapCut
 * Une seule page : source GPX + vidéos + widgets + export
 *
 * ⚠️  AVANT DISTRIBUTION — chercher "[DEV ONLY]" et supprimer ces blocs :
 *   1. import uploadAsync (ligne ~20)
 *   2. fonction pickVideoFile() complète
 *   3. Bouton DEV dans la zone vidéo (JSX)
 *   4. styles devBtn + devBtnText
 */

import {
  useRef, useState, useCallback, useEffect, useMemo,
} from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, ScrollView,
  Alert, Dimensions, PanResponder, ActivityIndicator,
  Modal, Linking,
} from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { Video, ResizeMode, AVPlaybackStatus } from 'expo-av';
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import { loadProjects, saveProject } from '../../lib/store';
import { uploadGpxFile } from '../../lib/api';
import { Project, ActivitySummary, Clip, WidgetPlacement } from '../../types';
// [DEV ONLY] — supprimer avant distribution (upload vidéo sans PhotosPicker natif)
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { uploadAsync, FileSystemUploadType } = require('expo-file-system/legacy');
// [/DEV ONLY]

const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1';
const { width: SCREEN_W } = Dimensions.get('window');
const VIDEO_H = Math.round(SCREEN_W * 16 / 9 * 0.45); // ~310px — portrait-friendly

// ─── Widgets catalogue ────────────────────────────────────────────────────────

interface WidgetDef {
  key: string;
  label: string;
  icon: string;
  category: string;
}

const CATEGORIES = ['Tous', 'Vitesse', 'Cardio', 'Terrain', 'Stats', 'GPS'];

const WIDGETS: WidgetDef[] = [
  { key: 'speed',          label: 'Vitesse',     icon: '⚡', category: 'Vitesse' },
  { key: 'pace',           label: 'Allure',      icon: '🏃', category: 'Vitesse' },
  { key: 'heart_rate',     label: 'FC',          icon: '❤️', category: 'Cardio' },
  { key: 'cadence',        label: 'Cadence',     icon: '🔄', category: 'Cardio' },
  { key: 'power',          label: 'Puissance',   icon: '⚡️', category: 'Cardio' },
  { key: 'altitude',       label: 'Altitude',    icon: '⛰️', category: 'Terrain' },
  { key: 'grade',          label: 'Pente',       icon: '📐', category: 'Terrain' },
  { key: 'elevation_gain', label: 'D+',          icon: '↗️', category: 'Terrain' },
  { key: 'distance',       label: 'Distance',    icon: '📍', category: 'Stats' },
  { key: 'elapsed',        label: 'Temps',       icon: '⏱️', category: 'Stats' },
  { key: 'temperature',    label: 'Température', icon: '🌡️', category: 'Stats' },
  { key: 'map',            label: 'Carte GPS',   icon: '🗺️', category: 'GPS' },
  { key: 'hr_graph',       label: 'Graphe FC',   icon: '📈', category: 'Cardio' },
  { key: 'alt_graph',      label: 'Graphe Alt.', icon: '📊', category: 'Terrain' },
];

// Default positions pour chaque nouveau widget (grille 3x3)
const DEFAULT_POSITIONS: [number, number][] = [
  [0.05, 0.05], [0.55, 0.05], [0.05, 0.75],
  [0.55, 0.75], [0.05, 0.40], [0.55, 0.40],
  [0.30, 0.05], [0.30, 0.75], [0.30, 0.40],
];

// ─── Composant widget draggable sur la vidéo ─────────────────────────────────

interface DraggableWidgetProps {
  widgetKey: string;
  label: string;
  icon: string;
  x: number; y: number;
  onMove: (key: string, x: number, y: number) => void;
  onRemove: (key: string) => void;
}

function DraggableWidget({ widgetKey, label, icon, x, y, onMove, onRemove }: DraggableWidgetProps) {
  const posRef = useRef({ x, y });
  const [pos, setPos] = useState({ x, y });

  const panResponder = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onPanResponderMove: (_, gs) => {
        const nx = Math.max(0, Math.min(0.85, posRef.current.x + gs.dx / SCREEN_W));
        const ny = Math.max(0, Math.min(0.85, posRef.current.y + gs.dy / VIDEO_H));
        setPos({ x: nx, y: ny });
      },
      onPanResponderRelease: (_, gs) => {
        const nx = Math.max(0, Math.min(0.85, posRef.current.x + gs.dx / SCREEN_W));
        const ny = Math.max(0, Math.min(0.85, posRef.current.y + gs.dy / VIDEO_H));
        posRef.current = { x: nx, y: ny };
        onMove(widgetKey, nx, ny);
      },
    })
  ).current;

  useEffect(() => { posRef.current = { x, y }; setPos({ x, y }); }, [x, y]);

  return (
    <View
      style={[styles.widgetOverlay, { left: pos.x * SCREEN_W, top: pos.y * VIDEO_H }]}
      {...panResponder.panHandlers}
    >
      <Text style={styles.widgetOverlayIcon}>{icon}</Text>
      <Text style={styles.widgetOverlayLabel}>{label}</Text>
      <TouchableOpacity style={styles.widgetRemoveBtn} onPress={() => onRemove(widgetKey)}>
        <Text style={styles.widgetRemoveX}>✕</Text>
      </TouchableOpacity>
    </View>
  );
}

// ─── Écran principal ──────────────────────────────────────────────────────────

export default function EditorScreen() {
  const { projectId } = useLocalSearchParams<{ projectId: string }>();
  const videoRef = useRef<Video>(null);

  // Projet
  const [project, setProject] = useState<Project | null>(null);

  // Vidéo
  const [clipIndex, setClipIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [position, setPosition] = useState(0);   // secondes dans le montage global
  const [totalDuration, setTotalDuration] = useState(0);

  // GPX offset
  const [offset, setOffset] = useState(0);

  // Widgets
  const [selectedCategory, setSelectedCategory] = useState('Tous');
  const [widgetLayout, setWidgetLayout] = useState<WidgetPlacement[]>([]);

  // UI state
  const [loading, setLoading] = useState(false);
  const [loadingMsg, setLoadingMsg] = useState('');
  const [stravaModal, setStravaModal] = useState(false);
  const [stravaActivities, setStravaActivities] = useState<StravaActivity[]>([]);
  const [stravaToken, setStravaToken] = useState<string | null>(null);

  // ── Chargement projet ──────────────────────────────────────────────────────
  useEffect(() => {
    (async () => {
      const projects = await loadProjects();
      const p = projects.find(x => x.id === projectId);
      if (p) {
        setProject(p);
        setWidgetLayout(p.widgetLayout ?? []);
        setOffset(p.offsetS ?? 0);
        const total = (p.clips ?? []).reduce((s, c) => s + c.duration_s, 0);
        setTotalDuration(total);
      }
    })();
  }, [projectId]);

  const save = useCallback(async (updated: Project) => {
    setProject(updated);
    await saveProject(updated);
  }, []);

  // ── Strava OAuth ───────────────────────────────────────────────────────────
  async function connectStrava() {
    setLoading(true); setLoadingMsg('Connexion à Strava…');
    try {
      const callbackBase = BASE_URL.replace('/api/v1', '');
      const res = await fetch(`${BASE_URL}/strava/start?callback_base=${encodeURIComponent(callbackBase)}`);
      if (!res.ok) throw new Error(`API ${res.status}`);
      const { auth_url, state } = await res.json() as { auth_url: string; state: string };
      await Linking.openURL(auth_url);
      setLoadingMsg('En attente de l\'autorisation…');
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const poll = await fetch(`${BASE_URL}/strava/poll/${state}`);
        if (!poll.ok) continue;
        const data = await poll.json() as { status: string; access_token?: string; athlete?: object; error?: string };
        if (data.status === 'error') throw new Error(data.error ?? 'Erreur Strava');
        if (data.status === 'done' && data.access_token) {
          setStravaToken(data.access_token);
          setLoadingMsg('Chargement des activités…');
          const acts = await fetchStravaActivities(data.access_token, 1);
          setStravaActivities(acts);
          setStravaModal(true);
          return;
        }
      }
      throw new Error('Timeout — 2 min écoulées');
    } catch (e) {
      Alert.alert('Erreur Strava', String(e));
    } finally {
      setLoading(false); setLoadingMsg('');
    }
  }

  async function fetchStravaActivities(token: string, page: number): Promise<StravaActivity[]> {
    const params = new URLSearchParams({ access_token: token, page: String(page), per_page: '20' });
    const res = await fetch(`${BASE_URL}/strava/activities?${params}`);
    if (!res.ok) throw new Error(`API ${res.status}`);
    const data = await res.json() as { activities: StravaActivity[] };
    return data.activities ?? [];
  }

  async function importStravaActivity(act: StravaActivity) {
    if (!stravaToken || !project) return;
    setStravaModal(false);
    setLoading(true); setLoadingMsg(`Import «${act.name}»…`);
    try {
      const res = await fetch(`${BASE_URL}/strava/activity/${act.id}/import?access_token=${stravaToken}`, { method: 'POST' });
      if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
      const data = await res.json() as { session_id: string; activity_summary: ActivitySummary };
      await save({
        ...project,
        sessionId: data.session_id,
        gpxSource: 'strava',
        activityName: act.name,
        activityDate: act.date,
        activitySummary: data.activity_summary,
      });
    } catch (e) {
      Alert.alert('Erreur import', String(e));
    } finally {
      setLoading(false); setLoadingMsg('');
    }
  }

  // ── Import GPX fichier ─────────────────────────────────────────────────────
  async function importGpxFile() {
    let asset: DocumentPicker.DocumentPickerAsset;
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ['application/gpx+xml', 'application/octet-stream', '*/*'],
        copyToCacheDirectory: true,
      });
      if (result.canceled || !result.assets?.[0]) return;
      asset = result.assets[0];
    } catch (e) {
      Alert.alert('Erreur', String(e)); return;
    }
    if (!asset.name.toLowerCase().endsWith('.gpx')) {
      Alert.alert('Fichier invalide', 'Sélectionnez un .gpx'); return;
    }
    if (!project) return;
    setLoading(true); setLoadingMsg('Upload GPX…');
    try {
      const data = await uploadGpxFile(asset.uri, asset.name) as {
        session_id: string; activity_summary: ActivitySummary;
      };
      await save({ ...project, sessionId: data.session_id, gpxSource: 'file', activitySummary: data.activity_summary });
    } catch (e) {
      Alert.alert('Erreur GPX', String(e));
    } finally {
      setLoading(false); setLoadingMsg('');
    }
  }

  // ── Ajout vidéo (galerie — nécessite build) ───────────────────────────────
  async function pickVideo() {
    if (!project?.sessionId) {
      Alert.alert('Source GPX manquante', 'Ajoutez d\'abord une source GPX.'); return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['videos'],
      allowsMultipleSelection: true,
      quality: 1,
    });
    if (result.canceled || !result.assets) return;
    setLoading(true); setLoadingMsg('Analyse vidéo…');
    try {
      const newClips: Clip[] = [];
      for (const asset of result.assets) {
        const filename = asset.uri.split('/').pop() ?? 'video.mov';
        newClips.push({
          filename,
          uri: asset.uri,
          duration_s: (asset.duration ?? 0) / 1000,
          fps: 30,
          width: asset.width ?? 1920,
          height: asset.height ?? 1080,
        });
      }
      const existing = project.clips ?? [];
      const allClips = [...existing, ...newClips];
      const total = allClips.reduce((s, c) => s + c.duration_s, 0);
      setTotalDuration(total);
      await save({ ...project, clips: allClips });
    } catch (e) {
      Alert.alert('Erreur vidéo', String(e));
    } finally {
      setLoading(false); setLoadingMsg('');
    }
  }

  // [DEV ONLY] — supprimer avant distribution
  async function pickVideoFile() {
    if (!project?.sessionId) {
      Alert.alert('Source GPX manquante', 'Ajoutez d\'abord une source GPX.'); return;
    }
    let asset: DocumentPicker.DocumentPickerAsset;
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ['video/*', 'application/octet-stream', '*/*'],
        copyToCacheDirectory: true,
      });
      if (result.canceled || !result.assets?.[0]) return;
      asset = result.assets[0];
    } catch (e) {
      Alert.alert('Erreur', String(e)); return;
    }
    setLoading(true); setLoadingMsg('[DEV] Upload vidéo vers serveur…');
    try {
      const filename = asset.name ?? asset.uri.split('/').pop() ?? 'video.mov';
      // Upload vers le backend (nécessaire pour que /render/start trouve le fichier)
      const up = await uploadAsync(
        `${BASE_URL}/render/upload-video/${project.sessionId}`,
        asset.uri,
        { httpMethod: 'POST', uploadType: FileSystemUploadType.MULTIPART, fieldName: 'file', mimeType: 'video/quicktime' }
      );
      if (up.status < 200 || up.status >= 300) throw new Error(`Upload ${up.status}: ${up.body}`);
      const newClip: Clip = { filename, uri: asset.uri, duration_s: 30, fps: 30, width: 1920, height: 1080 };
      const allClips = [...(project.clips ?? []), newClip];
      setTotalDuration(allClips.reduce((s, c) => s + c.duration_s, 0));
      await save({ ...project, clips: allClips });
    } catch (e) {
      Alert.alert('[DEV] Erreur upload vidéo', String(e));
    } finally {
      setLoading(false); setLoadingMsg('');
    }
  }
  // [/DEV ONLY]

  // ── Lecture vidéo ─────────────────────────────────────────────────────────
  function onPlaybackUpdate(status: AVPlaybackStatus) {
    if (!status.isLoaded) return;
    const clips = project?.clips ?? [];
    const precedingDuration = clips.slice(0, clipIndex).reduce((s, c) => s + c.duration_s, 0);
    setPosition(precedingDuration + (status.positionMillis ?? 0) / 1000);
    if (status.didJustFinish) {
      const nextIdx = clipIndex + 1;
      if (nextIdx < clips.length) setClipIndex(nextIdx);
      else { setIsPlaying(false); setClipIndex(0); }
    }
  }

  async function togglePlay() {
    if (!videoRef.current) return;
    if (isPlaying) { await videoRef.current.pauseAsync(); setIsPlaying(false); }
    else { await videoRef.current.playAsync(); setIsPlaying(true); }
  }

  async function seekTo(seconds: number) {
    const clips = project?.clips ?? [];
    let acc = 0;
    for (let i = 0; i < clips.length; i++) {
      if (seconds < acc + clips[i].duration_s) {
        if (i !== clipIndex) setClipIndex(i);
        await videoRef.current?.setPositionAsync((seconds - acc) * 1000);
        break;
      }
      acc += clips[i].duration_s;
    }
  }

  // ── Widgets ───────────────────────────────────────────────────────────────
  function toggleWidget(key: string) {
    const exists = widgetLayout.find(w => w.key === key);
    let updated: WidgetPlacement[];
    if (exists) {
      updated = widgetLayout.filter(w => w.key !== key);
    } else {
      const idx = widgetLayout.length % DEFAULT_POSITIONS.length;
      const [x, y] = DEFAULT_POSITIONS[idx];
      updated = [...widgetLayout, { key, x, y }];
    }
    setWidgetLayout(updated);
    if (project) save({ ...project, widgetLayout: updated });
  }

  function moveWidget(key: string, x: number, y: number) {
    const updated = widgetLayout.map(w => w.key === key ? { ...w, x, y } : w);
    setWidgetLayout(updated);
    if (project) save({ ...project, widgetLayout: updated });
  }

  // ── Export ────────────────────────────────────────────────────────────────
  async function startExport() {
    if (!project?.sessionId) { Alert.alert('Manquant', 'Source GPX requise.'); return; }
    if (!project.clips?.length) { Alert.alert('Manquant', 'Ajoutez au moins une vidéo.'); return; }
    if (!widgetLayout.length) { Alert.alert('Manquant', 'Sélectionnez au moins un widget.'); return; }
    setLoading(true); setLoadingMsg('Démarrage du rendu…');
    try {
      // Sync via /videos/metadata
      const videos = (project.clips ?? []).map(c => ({
        filename: c.filename, duration_s: c.duration_s, fps: c.fps,
        width: c.width, height: c.height, creation_time: c.creation_time,
      }));
      const syncRes = await fetch(`${BASE_URL}/videos/metadata`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: project.sessionId, videos, global_offset_s: offset }),
      });
      if (!syncRes.ok) throw new Error(`Sync ${syncRes.status}: ${await syncRes.text()}`);

      // Render — format backend: { session_id, filename, widget_layout, offset_s }
      const firstClip = (project.clips ?? [])[0];
      const renderRes = await fetch(`${BASE_URL}/render/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: project.sessionId,
          filename: firstClip.filename,
          widget_layout: widgetLayout.map(w => ({ key: w.key, x: w.x, y: w.y })),
          offset_s: offset,
        }),
      });
      if (!renderRes.ok) throw new Error(`Render ${renderRes.status}: ${await renderRes.text()}`);
      const { job_id } = await renderRes.json() as { job_id: string };

      // Polling
      setLoadingMsg('Rendu en cours…');
      for (let i = 0; i < 300; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const statusRes = await fetch(`${BASE_URL}/render/status/${job_id}`);
        const st = await statusRes.json() as { status: string; progress_pct?: number };
        if (st.status === 'done') {
          setLoading(false);
          Alert.alert('✅ Terminé !', 'Vidéo rendue. Téléchargez depuis :\n' + `${BASE_URL}/render/download/${job_id}`);
          return;
        }
        if (st.status === 'error') throw new Error('Erreur rendu serveur');
        if (st.progress_pct) setLoadingMsg(`Rendu ${Math.round(st.progress_pct)}%…`);
      }
      throw new Error('Timeout rendu');
    } catch (e) {
      Alert.alert('Erreur export', String(e));
    } finally {
      setLoading(false); setLoadingMsg('');
    }
  }

  // ── Rendu ─────────────────────────────────────────────────────────────────
  const currentClip = (project?.clips ?? [])[clipIndex];
  // Mémoïse la source pour éviter le remount de Video à chaque save projet
  const videoSource = useMemo(
    () => currentClip ? { uri: currentClip.uri } : undefined,
    [currentClip?.uri] // eslint-disable-line react-hooks/exhaustive-deps
  );
  const filteredWidgets = selectedCategory === 'Tous'
    ? WIDGETS
    : WIDGETS.filter(w => w.category === selectedCategory);

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60); const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, '0')}`;
  };

  return (
    <View style={styles.container}>

      {/* ── Header ── */}
      <View style={styles.header}>
        <Text style={styles.headerTitle}>GPX OVERLAY</Text>
        {project?.activityName && (
          <Text style={styles.headerActivity} numberOfLines={1}>{project.activityName}</Text>
        )}
      </View>

      {/* ── Source bar ── */}
      <View style={styles.sourceBar}>
        <TouchableOpacity
          style={[styles.sourceBtn, project?.sessionId ? styles.sourceBtnDone : styles.sourceBtnStrava]}
          onPress={project?.sessionId ? () => setStravaModal(true) : connectStrava}
        >
          <Text style={styles.sourceBtnText}>
            {project?.sessionId ? '🟠 Changer activité' : '🟠 Strava'}
          </Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.sourceBtnGpx} onPress={importGpxFile}>
          <Text style={styles.sourceBtnText}>📂 GPX</Text>
        </TouchableOpacity>
      </View>

      {/* ── Zone vidéo ── */}
      <View style={[styles.videoZone, { height: VIDEO_H }]}>
        {/* Bouton DEV toujours visible en haut à droite */}
        <TouchableOpacity style={styles.devBtn} onPress={pickVideoFile}>
          <Text style={styles.devBtnText}>＋ DEV</Text>
        </TouchableOpacity>

        {videoSource && (
          <Video
            ref={videoRef}
            source={videoSource}
            style={StyleSheet.absoluteFill}
            resizeMode={ResizeMode.COVER}
            onPlaybackStatusUpdate={onPlaybackUpdate}
            useNativeControls={false}
          />
        )}

        {/* Widgets overlay */}
        {widgetLayout.map(placement => {
          const def = WIDGETS.find(w => w.key === placement.key);
          if (!def) return null;
          return (
            <DraggableWidget
              key={placement.key}
              widgetKey={placement.key}
              label={def.label}
              icon={def.icon}
              x={placement.x}
              y={placement.y}
              onMove={moveWidget}
              onRemove={toggleWidget}
            />
          );
        })}

        {!videoSource && (
          <TouchableOpacity style={styles.addVideoBtn} onPress={pickVideoFile}>
            <Text style={styles.addVideoIcon}>🎬</Text>
            <Text style={styles.addVideoText}>Ajouter une vidéo</Text>
          </TouchableOpacity>
        )}

        {/* Bouton + clip si vidéo déjà chargée */}
        {videoSource && (
          <TouchableOpacity style={styles.addMoreVideoBtn} onPress={pickVideo}>
            <Text style={styles.addMoreVideoText}>+ Clip</Text>
          </TouchableOpacity>
        )}
      </View>

      {/* ── Contrôles ── */}
      <View style={styles.controls}>
        {/* Bouton play + timeline */}
        <View style={styles.timelineRow}>
          <TouchableOpacity onPress={togglePlay} style={styles.playBtn}>
            <Text style={styles.playBtnText}>{isPlaying ? '⏸' : '▶️'}</Text>
          </TouchableOpacity>
          <View style={styles.sliderWrapper}>
            <View style={styles.sliderTrack}>
              <View style={[styles.sliderFill, {
                width: totalDuration > 0 ? `${(position / totalDuration) * 100}%` : '0%',
              }]} />
              <TouchableOpacity
                style={[styles.sliderThumb, {
                  left: totalDuration > 0 ? `${(position / totalDuration) * 100}%` : '0%',
                }]}
                onStartShouldSetResponder={() => true}
              />
            </View>
            <Text style={styles.timeText}>{formatTime(position)} / {formatTime(totalDuration)}</Text>
          </View>
        </View>

        {/* Offset slider */}
        <View style={styles.offsetRow}>
          <Text style={styles.offsetLabel}>GPX offset</Text>
          <TouchableOpacity style={styles.offsetBtn} onPress={() => setOffset(o => Math.max(-90, o - 1))}>
            <Text style={styles.offsetBtnText}>-1s</Text>
          </TouchableOpacity>
          <Text style={styles.offsetValue}>{offset > 0 ? '+' : ''}{offset.toFixed(1)}s</Text>
          <TouchableOpacity style={styles.offsetBtn} onPress={() => setOffset(o => Math.min(90, o + 1))}>
            <Text style={styles.offsetBtnText}>+1s</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.offsetBtn} onPress={() => setOffset(0)}>
            <Text style={styles.offsetBtnText}>reset</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* ── Action bar ── */}
      <View style={styles.actionBar}>
        <TouchableOpacity style={styles.actionBtnFrame}>
          <Text style={styles.actionBtnText}>📍 Frame Match</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={[styles.actionBtnExport, !project?.sessionId && styles.actionBtnDisabled]}
          onPress={startExport}
          disabled={!project?.sessionId}
        >
          <Text style={styles.actionBtnText}>🎬 Exporter</Text>
        </TouchableOpacity>
      </View>

      {/* ── Widget drawer ── */}
      <View style={styles.drawer}>
        {/* Catégories */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.catScroll} contentContainerStyle={styles.catContent}>
          {CATEGORIES.map(cat => (
            <TouchableOpacity
              key={cat}
              style={[styles.catChip, selectedCategory === cat && styles.catChipActive]}
              onPress={() => setSelectedCategory(cat)}
            >
              <Text style={[styles.catChipText, selectedCategory === cat && styles.catChipTextActive]}>
                {cat}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>

        {/* Widgets */}
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.widgetListContent}>
          {filteredWidgets.map(w => {
            const active = widgetLayout.some(p => p.key === w.key);
            return (
              <TouchableOpacity
                key={w.key}
                style={[styles.widgetChip, active && styles.widgetChipActive]}
                onPress={() => toggleWidget(w.key)}
              >
                <Text style={styles.widgetChipIcon}>{w.icon}</Text>
                <Text style={[styles.widgetChipLabel, active && styles.widgetChipLabelActive]}>
                  {w.label}
                </Text>
                {active && <View style={styles.widgetActiveDot} />}
              </TouchableOpacity>
            );
          })}
        </ScrollView>
      </View>

      {/* ── Loading overlay ── */}
      {loading && (
        <View style={styles.loadingOverlay}>
          <ActivityIndicator size="large" color="#4af" />
          <Text style={styles.loadingText}>{loadingMsg}</Text>
        </View>
      )}

      {/* ── Modal Strava activités ── */}
      <Modal visible={stravaModal} animationType="slide" transparent>
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Choisir une activité</Text>
              <TouchableOpacity onPress={() => setStravaModal(false)}>
                <Text style={styles.modalClose}>✕</Text>
              </TouchableOpacity>
            </View>
            <ScrollView>
              {stravaActivities.map(act => (
                <TouchableOpacity key={act.id} style={styles.actRow} onPress={() => importStravaActivity(act)}>
                  <Text style={styles.actIcon}>{actIcon(act.type)}</Text>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.actName} numberOfLines={1}>{act.name}</Text>
                    <Text style={styles.actMeta}>
                      {act.distance_km.toFixed(1)} km · {formatDurS(act.duration_s)}
                      {act.avg_hr ? ` · ❤️ ${Math.round(act.avg_hr)}` : ''}
                    </Text>
                  </View>
                  <Text style={styles.actChevron}>›</Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
          </View>
        </View>
      </Modal>
    </View>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

interface StravaActivity {
  id: number; name: string; type: string; date: string;
  distance_km: number; duration_s: number; elevation_m: number; avg_hr?: number;
}

function actIcon(type: string) {
  const m: Record<string, string> = { Run: '🏃', Ride: '🚴', Hike: '🥾', Walk: '🚶', TrailRun: '🏔️' };
  return m[type] ?? '🏅';
}

function formatDurS(s: number) {
  const h = Math.floor(s / 3600); const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h${String(m).padStart(2, '0')}` : `${m}min`;
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  container:          { flex: 1, backgroundColor: '#111' },

  header:             { paddingTop: 56, paddingHorizontal: 16, paddingBottom: 8, flexDirection: 'row', alignItems: 'baseline', gap: 10 },
  headerTitle:        { color: '#fff', fontSize: 20, fontWeight: 'bold', letterSpacing: 1 },
  headerActivity:     { color: '#4af', fontSize: 13, flex: 1 },

  sourceBar:          { flexDirection: 'row', gap: 8, paddingHorizontal: 12, paddingBottom: 8 },
  sourceBtn:          { flex: 1, borderRadius: 8, padding: 10, alignItems: 'center' },
  sourceBtnStrava:    { backgroundColor: '#2a1505' },
  sourceBtnDone:      { backgroundColor: '#1a2a05' },
  sourceBtnGpx:       { flex: 1, backgroundColor: '#05152a', borderRadius: 8, padding: 10, alignItems: 'center' },
  sourceBtnText:      { color: '#fff', fontSize: 14, fontWeight: '600' },

  videoZone:          { backgroundColor: '#000', width: SCREEN_W, position: 'relative' },
  addVideoBtn:        { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8 },
  addVideoIcon:       { fontSize: 36 },
  addVideoText:       { color: '#666', fontSize: 16 },
  addMoreVideoBtn:    { position: 'absolute', bottom: 8, left: 8, backgroundColor: '#ffffff22', borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6 },
  addMoreVideoText:   { color: '#fff', fontSize: 13, fontWeight: '600' },
  devBtn:             { position: 'absolute', top: 8, right: 8, backgroundColor: '#e65c00cc', borderRadius: 6, paddingHorizontal: 10, paddingVertical: 5, zIndex: 10 },
  devBtnText:         { color: '#fff', fontSize: 12, fontWeight: 'bold' },

  widgetOverlay:      { position: 'absolute', backgroundColor: '#000c', borderRadius: 8, padding: 6, flexDirection: 'row', alignItems: 'center', gap: 4, minWidth: 60 },
  widgetOverlayIcon:  { fontSize: 14 },
  widgetOverlayLabel: { color: '#fff', fontSize: 11, fontWeight: '600' },
  widgetRemoveBtn:    { marginLeft: 4, padding: 2 },
  widgetRemoveX:      { color: '#f44', fontSize: 10, fontWeight: 'bold' },

  controls:           { paddingHorizontal: 12, paddingVertical: 8, backgroundColor: '#181818', gap: 8 },
  timelineRow:        { flexDirection: 'row', alignItems: 'center', gap: 10 },
  playBtn:            { width: 36, height: 36, alignItems: 'center', justifyContent: 'center' },
  playBtnText:        { fontSize: 20 },
  sliderWrapper:      { flex: 1, gap: 4 },
  sliderTrack:        { height: 4, backgroundColor: '#333', borderRadius: 2, position: 'relative' },
  sliderFill:         { height: 4, backgroundColor: '#4af', borderRadius: 2 },
  sliderThumb:        { position: 'absolute', width: 14, height: 14, borderRadius: 7, backgroundColor: '#4af', top: -5, marginLeft: -7 },
  timeText:           { color: '#666', fontSize: 11 },

  offsetRow:          { flexDirection: 'row', alignItems: 'center', gap: 8 },
  offsetLabel:        { color: '#888', fontSize: 12, flex: 1 },
  offsetBtn:          { backgroundColor: '#2a2a2a', borderRadius: 6, paddingHorizontal: 10, paddingVertical: 5 },
  offsetBtnText:      { color: '#aaa', fontSize: 12, fontWeight: '600' },
  offsetValue:        { color: '#f90', fontSize: 14, fontWeight: 'bold', minWidth: 50, textAlign: 'center' },

  actionBar:          { flexDirection: 'row', gap: 8, paddingHorizontal: 12, paddingVertical: 8, backgroundColor: '#181818', borderTopWidth: 1, borderTopColor: '#222' },
  actionBtnFrame:     { flex: 1, backgroundColor: '#1a1a2a', borderRadius: 10, padding: 12, alignItems: 'center' },
  actionBtnExport:    { flex: 1, backgroundColor: '#4af', borderRadius: 10, padding: 12, alignItems: 'center' },
  actionBtnDisabled:  { backgroundColor: '#333' },
  actionBtnText:      { color: '#fff', fontSize: 14, fontWeight: 'bold' },

  drawer:             { height: 150, backgroundColor: '#0e0e0e' },
  catScroll:          { flexGrow: 0 },
  catContent:         { paddingHorizontal: 12, paddingVertical: 8, gap: 6 },
  catChip:            { paddingHorizontal: 14, paddingVertical: 6, borderRadius: 20, backgroundColor: '#1e1e1e' },
  catChipActive:      { backgroundColor: '#4af' },
  catChipText:        { color: '#888', fontSize: 13 },
  catChipTextActive:  { color: '#fff', fontWeight: '600' },

  widgetListContent:  { paddingHorizontal: 12, paddingBottom: 8, gap: 8, alignItems: 'center' },
  widgetChip:         { alignItems: 'center', justifyContent: 'center', backgroundColor: '#1e1e1e', borderRadius: 12, padding: 10, width: 76, height: 76, position: 'relative' },
  widgetChipActive:   { backgroundColor: '#0a2a4a', borderWidth: 1.5, borderColor: '#4af' },
  widgetChipIcon:     { fontSize: 20, marginBottom: 4 },
  widgetChipLabel:    { color: '#888', fontSize: 11, textAlign: 'center' },
  widgetChipLabelActive: { color: '#4af', fontWeight: '600' },
  widgetActiveDot:    { position: 'absolute', top: 5, right: 5, width: 7, height: 7, borderRadius: 4, backgroundColor: '#4af' },

  loadingOverlay:     { ...StyleSheet.absoluteFillObject, backgroundColor: '#000b', alignItems: 'center', justifyContent: 'center', gap: 12 },
  loadingText:        { color: '#fff', fontSize: 15 },

  modalOverlay:       { flex: 1, backgroundColor: '#000a', justifyContent: 'flex-end' },
  modalBox:           { backgroundColor: '#1a1a1a', borderTopLeftRadius: 20, borderTopRightRadius: 20, maxHeight: '80%' },
  modalHeader:        { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', padding: 20, borderBottomWidth: 1, borderBottomColor: '#2a2a2a' },
  modalTitle:         { color: '#fff', fontSize: 18, fontWeight: 'bold' },
  modalClose:         { color: '#666', fontSize: 20, padding: 4 },
  actRow:             { flexDirection: 'row', alignItems: 'center', padding: 16, gap: 12, borderBottomWidth: 1, borderBottomColor: '#222' },
  actIcon:            { fontSize: 24 },
  actName:            { color: '#fff', fontSize: 15, fontWeight: '500', marginBottom: 3 },
  actMeta:            { color: '#666', fontSize: 12 },
  actChevron:         { color: '#444', fontSize: 22 },
});
