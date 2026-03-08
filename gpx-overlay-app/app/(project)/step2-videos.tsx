/**
 * STEP 2 — Sélection des vidéos
 * Multi-pick depuis la galerie Photos.
 * Affiche vignette + date/durée.
 * Détecte les clips incompatibles avec la course GPX.
 *
 * ⚠️  IMPORTANT : vidéos originales iPhone uniquement — les .MOV natifs.
 * Un clip réexporté ou recadré perd sa creation_time → sync impossible.
 */

import { useState, useCallback } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, FlatList,
  Image, Alert, ActivityIndicator, ScrollView,
} from 'react-native';
import { useRouter, useLocalSearchParams, useFocusEffect } from 'expo-router';
import * as DocumentPicker from 'expo-document-picker';
import { loadProjects, saveProject } from '../../lib/store';
import { submitVideoMetadata } from '../../lib/api';
import { Project, Clip } from '../../types';
import StepNav from '../../components/StepNav';

const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1';

export default function Step2Screen() {
  const router = useRouter();
  const { projectId } = useLocalSearchParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [testMode, setTestMode] = useState(false);

  useFocusEffect(
    useCallback(() => {
      loadProjects().then(projects => {
        const p = projects.find(x => x.id === projectId) ?? null;
        setProject(p);
        if (p?.clips) setClips(p.clips);
      });
    }, [projectId])
  );

  // ── Sélection + upload des clips via document picker ─────────────────────
  async function pickVideos() {
    if (!project?.sessionId) {
      Alert.alert('Session manquante', 'Retournez à l\'étape 1 pour uploader le GPX.');
      return;
    }

    const result = await DocumentPicker.getDocumentAsync({
      type: ['public.movie', 'public.mpeg-4', 'com.apple.quicktime-movie', 'video/*'],
      copyToCacheDirectory: true,
      multiple: true,
    });

    if (result.canceled || !result.assets?.length) return;

    setLoading(true);
    const newClips: Clip[] = [];

    for (const asset of result.assets) {
      const filename = asset.name;
      setStatus(`Upload ${filename} pour extraction métadonnées…`);

      try {
        const formData = new FormData();
        formData.append('file', {
          uri: asset.uri,
          name: filename,
          type: 'video/quicktime',
        } as unknown as Blob);

        const res = await fetch(
          `${BASE_URL}/videos/upload-and-probe/${project.sessionId}`,
          { method: 'POST', body: formData }
        );

        if (!res.ok) throw new Error(await res.text());
        const meta = await res.json() as {
          filename: string; fps: number; duration_s: number;
          width: number; height: number; creation_time?: string;
          probe_error?: string;
        };

        if (meta.probe_error) {
          Alert.alert('Avertissement', `${filename} : ${meta.probe_error}\nMétadonnées par défaut utilisées.`);
        }

        newClips.push({
          filename: meta.filename,
          uri: asset.uri,
          duration_s: meta.duration_s,
          fps: meta.fps,
          width: meta.width,
          height: meta.height,
          creation_time: meta.creation_time,
        });

      } catch (e) {
        Alert.alert('Erreur upload', `${filename} : ${String(e)}`);
      }
    }

    const merged = [...clips, ...newClips].filter(
      (c, i, arr) => arr.findIndex(x => x.filename === c.filename) === i
    );
    setClips(merged);
    setLoading(false);
    setStatus('');
  }

  function removeClip(filename: string) {
    setClips(prev => prev.filter(c => c.filename !== filename));
  }

  // ── Envoi des métadonnées au backend → calcul sync ─────────────────────────
  async function analyzeSync() {
    if (!project || !clips.length) return;

    // Vérification que les clips ont des métadonnées de date
    const clipsWithoutDate = clips.filter(c => !c.creation_time);
    if (clipsWithoutDate.length > 0) {
      Alert.alert(
        'Métadonnées manquantes',
        `${clipsWithoutDate.length} clip(s) n'ont pas de date de création détectée. La synchronisation pourrait échouer.\n\nAssurez-vous d'utiliser les vidéos ORIGINALES de votre iPhone, pas des copies réexportées.`,
        [
          { text: 'Annuler', style: 'cancel' },
          { text: 'Continuer quand même', onPress: () => doSync() },
        ]
      );
      return;
    }

    doSync();
  }

  async function doSync() {
    if (!project) return;
    setLoading(true);
    setStatus('Synchronisation avec le serveur...');

    try {
      const payload = {
        session_id: project.sessionId!,
        global_offset_s: testMode ? -67.1 : 0.0,
        videos: clips.map(c => ({
          filename: c.filename,
          duration_s: c.duration_s,
          fps: c.fps,
          width: c.width,
          height: c.height,
          creation_time: c.creation_time,
          timezone_offset_h: c.timezone_offset_h,
          codec: c.codec,
        })),
      };

      const syncData = await submitVideoMetadata(payload) as {
        session_id: string;
        gpx_start: string;
        gpx_end: string;
        global_offset_s: number;
        video_count: number;
        videos: {
          filename: string;
          coverage_pct: number;
          sync_confidence: number;
          gpx_segment_start?: string;
          gpx_segment_end?: string;
          has_data: boolean;
          error?: string;
        }[];
        warnings: string[];
      };

      // Merge statuts sync dans les clips
      const updatedClips = clips.map(c => {
        const sv = syncData.videos.find(v => v.filename === c.filename);
        if (!sv) return c;
        const coverage = sv.coverage_pct;
        return {
          ...c,
          syncStatus: (coverage > 70 ? 'ok' : coverage > 20 ? 'warning' : 'error') as Clip['syncStatus'],
          coveragePct: sv.coverage_pct,
          syncConfidence: sv.sync_confidence,
          syncError: sv.error,
        };
      });

      const updatedProject: Project = {
        ...project,
        clips: updatedClips,
        syncResult: {
          sessionId: syncData.session_id,
          gpxStart: syncData.gpx_start,
          gpxEnd: syncData.gpx_end,
          globalOffsetS: syncData.global_offset_s,
          videos: syncData.videos,
          warnings: syncData.warnings,
        },
      };

      await saveProject(updatedProject);

      router.push({
        pathname: '/(project)/step3-sync',
        params: { projectId },
      });
    } catch (e: unknown) {
      Alert.alert('Erreur serveur', String(e));
    } finally {
      setLoading(false);
      setStatus('');
    }
  }

  // ── Données de test Central Park (bypass picker pour Expo Go) ───────────────
  function loadTestClips() {
    const testClips: Clip[] = [
      { filename: 'IMG_5979.MOV', uri: '', duration_s: 47, fps: 60, width: 1080, height: 1920, creation_time: '2025-12-04T13:53:25Z' },
      { filename: 'IMG_5980.MOV', uri: '', duration_s: 32, fps: 60, width: 1080, height: 1920, creation_time: '2025-12-04T13:54:15Z' },
      { filename: 'IMG_5981.MOV', uri: '', duration_s: 28, fps: 60, width: 1080, height: 1920, creation_time: '2025-12-04T13:55:10Z' },
      { filename: 'IMG_5991.MOV', uri: '', duration_s: 35, fps: 60, width: 1080, height: 1920, creation_time: '2025-12-04T14:00:22Z' },
    ];
    setClips(testClips);
    setTestMode(true);
    Alert.alert('✅ Clips de test chargés', '4 clips Central Park du 4 déc 2025 prêts pour la sync.\n\nOffset Garmin -67.1s appliqué automatiquement.');
  }

  function formatDuration(s: number) {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  return (
    <View style={s.container}>
      <StepNav current="step2-videos" projectId={projectId as string} />
      {/* Avertissement important */}
      <View style={s.warning}>
        <Text style={s.warningText}>
          ⚠️  Utilisez uniquement les vidéos <Text style={s.bold}>originales</Text> de votre iPhone (.MOV). Toute copie, montage ou recadrage efface les métadonnées de synchronisation.
        </Text>
      </View>

      <ScrollView contentContainerStyle={{ padding: 16 }}>
        {/* Bouton ajouter */}
        <TouchableOpacity style={s.addBtn} onPress={pickVideos} disabled={loading}>
          <Text style={s.addBtnText}>+ Ajouter des vidéos</Text>
        </TouchableOpacity>

        {/* Mode démo — bypass picker pour Expo Go */}
        <TouchableOpacity style={s.testBtn} onPress={loadTestClips} disabled={loading}>
          <Text style={s.testBtnText}>🧪 Charger clips de test (Central Park)</Text>
        </TouchableOpacity>

        {/* Liste des clips */}
        {clips.map(clip => (
          <View key={clip.filename} style={s.clipCard}>
            <View style={s.clipThumb}>
              <Text style={s.clipThumbIcon}>🎬</Text>
            </View>
            <View style={s.clipInfo}>
              <Text style={s.clipName} numberOfLines={1}>{clip.filename}</Text>
              <Text style={s.clipMeta}>
                {formatDuration(clip.duration_s)} · {clip.width}×{clip.height}
              </Text>
              {clip.creation_time ? (
                <Text style={s.clipDate}>
                  {new Date(clip.creation_time).toLocaleString('fr-FR')}
                </Text>
              ) : (
                <Text style={s.clipNoDate}>⚠️  Pas de date détectée</Text>
              )}
            </View>
            <TouchableOpacity onPress={() => removeClip(clip.filename)}>
              <Text style={s.clipRemove}>✕</Text>
            </TouchableOpacity>
          </View>
        ))}

        {clips.length === 0 && !loading && (
          <Text style={s.empty}>
            Aucune vidéo sélectionnée.{'\n'}Ajoutez les clips de votre course.
          </Text>
        )}

        {loading && (
          <View style={s.loading}>
            <ActivityIndicator color="#4af" />
            <Text style={s.loadingText}>{status}</Text>
          </View>
        )}
      </ScrollView>

      {/* Bouton suivant */}
      {clips.length > 0 && !loading && (
        <TouchableOpacity style={s.nextBtn} onPress={analyzeSync}>
          <Text style={s.nextBtnText}>
            Analyser {clips.length} clip{clips.length > 1 ? 's' : ''} →
          </Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  container:    { flex: 1, backgroundColor: '#111' },
  warning:      { backgroundColor: '#2a1a00', margin: 12, borderRadius: 10, padding: 12 },
  warningText:  { color: '#f90', fontSize: 13, lineHeight: 18 },
  bold:         { fontWeight: 'bold' },
  addBtn:       { backgroundColor: '#1e3a4a', borderRadius: 12, padding: 16, alignItems: 'center', marginBottom: 8 },
  addBtnText:   { color: '#4af', fontSize: 16, fontWeight: '600' },
  testBtn:      { backgroundColor: '#1a2a1a', borderRadius: 12, padding: 12, alignItems: 'center', marginBottom: 16 },
  testBtnText:  { color: '#4f4', fontSize: 14 },
  clipCard:     { flexDirection: 'row', backgroundColor: '#1e1e1e', borderRadius: 12, padding: 12, marginBottom: 10, alignItems: 'center' },
  clipThumb:    { width: 52, height: 52, backgroundColor: '#2a2a2a', borderRadius: 8, alignItems: 'center', justifyContent: 'center', marginRight: 12 },
  clipThumbIcon:{ fontSize: 22 },
  clipInfo:     { flex: 1 },
  clipName:     { color: '#fff', fontSize: 14, fontWeight: '500', marginBottom: 2 },
  clipMeta:     { color: '#666', fontSize: 12, marginBottom: 2 },
  clipDate:     { color: '#4af', fontSize: 12 },
  clipNoDate:   { color: '#f90', fontSize: 12 },
  clipRemove:   { color: '#555', fontSize: 18, padding: 8 },
  empty:        { color: '#555', textAlign: 'center', marginTop: 40, lineHeight: 24 },
  loading:      { alignItems: 'center', padding: 24 },
  loadingText:  { color: '#888', marginTop: 8, fontSize: 14 },
  nextBtn:      { margin: 16, backgroundColor: '#4af', borderRadius: 14, padding: 16, alignItems: 'center' },
  nextBtnText:  { color: '#fff', fontSize: 17, fontWeight: 'bold' },
});
