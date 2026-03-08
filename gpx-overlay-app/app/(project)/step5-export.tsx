/**
 * STEP 5 — Export (mode dev serveur)
 *
 * Flow réel sans build natif :
 *   1. expo-document-picker → sélection vidéo depuis Files
 *   2. Upload multipart vers backend /render/upload-video
 *   3. POST /render/start → job_id
 *   4. Polling /render/status/{job_id} toutes les 2s
 *   5. Download via expo-file-system
 *   6. Sauvegarde dans Photos via expo-media-library
 */

import { useState, useCallback, useRef } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity,
  Alert, ActivityIndicator, Clipboard,
} from 'react-native';
import { useLocalSearchParams, useFocusEffect } from 'expo-router';
import * as DocumentPicker from 'expo-document-picker';
import * as MediaLibrary from 'expo-media-library';
import * as FileSystem from 'expo-file-system';
import * as Linking from 'expo-linking';
import { loadProjects, saveProject } from '../../lib/store';
import { Project } from '../../types';
import StepNav from '../../components/StepNav';

const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1';

type JobStatus = 'idle' | 'picking' | 'uploading' | 'rendering' | 'downloading' | 'saving' | 'done' | 'error';

interface RenderJob {
  clipFilename: string;
  jobId?: string;
  status: JobStatus;
  progress: number;    // 0..100
  message: string;
  outputUri?: string;
  error?: string;
}

export default function Step5Screen() {
  const { projectId } = useLocalSearchParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [jobs, setJobs] = useState<RenderJob[]>([]);
  const pollingRefs = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  useFocusEffect(
    useCallback(() => {
      loadProjects().then(projects => {
        const p = projects.find(x => x.id === projectId) ?? null;
        setProject(p);
        // Initialise un job par clip synchronisé
        if (p?.clips) {
          const okClips = p.clips.filter(c => c.syncStatus === 'ok' || c.syncStatus === 'warning');
          setJobs(okClips.map(c => ({
            clipFilename: c.filename,
            status: 'idle',
            progress: 0,
            message: 'En attente',
          })));
        }
      });

      return () => {
        // Nettoyage des pollers
        Object.values(pollingRefs.current).forEach(clearInterval);
        pollingRefs.current = {};
      };
    }, [projectId])
  );

  function updateJob(filename: string, patch: Partial<RenderJob>) {
    setJobs(prev => prev.map(j => j.clipFilename === filename ? { ...j, ...patch } : j));
  }

  // ── Flow complet pour un clip ────────────────────────────────────────────
  async function renderClip(clipFilename: string) {
    if (!project?.sessionId || !project.widgetLayout) return;

    try {
      // Vérifie si la vidéo est déjà sur le serveur (uploadée à step 2)
      const checkRes = await fetch(`${BASE_URL}/render/check-video/${project.sessionId}/${encodeURIComponent(clipFilename)}`);
      const alreadyUploaded = checkRes.ok && (await checkRes.json()).exists;

      if (!alreadyUploaded) {
        // 1. Sélection fichier
        updateJob(clipFilename, { status: 'picking', message: 'Sélectionnez la vidéo dans Files…' });

        const picked = await DocumentPicker.getDocumentAsync({
          type: ['public.movie', 'public.mpeg-4', 'com.apple.quicktime-movie', 'video/*'],
          copyToCacheDirectory: true,
        });

        if (picked.canceled || !picked.assets?.[0]) {
          updateJob(clipFilename, { status: 'idle', message: 'Annulé', progress: 0 });
          return;
        }

        // 2. Upload vers le backend
        updateJob(clipFilename, { status: 'uploading', message: 'Upload en cours…', progress: 5 });

        const formData = new FormData();
        formData.append('file', {
          uri: picked.assets[0].uri,
          name: clipFilename,
          type: 'video/quicktime',
        } as unknown as Blob);

        const uploadRes = await fetch(
          `${BASE_URL}/render/upload-video/${project.sessionId}`,
          { method: 'POST', body: formData }
        );
        if (!uploadRes.ok) throw new Error(`Upload échoué: ${await uploadRes.text()}`);
      }

      updateJob(clipFilename, { progress: 20, message: 'Vidéo prête — lancement du rendu…' });

      // 3. Démarrer le rendu
      const layout = project.widgetLayout.map(w => ({
        key: w.key, x: w.x, y: w.y, anchor: 'top-left',
      }));

      const startRes = await fetch(`${BASE_URL}/render/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: project.sessionId,
          filename: clipFilename,
          widget_layout: layout,
          quality: 'medium',
        }),
      });
      if (!startRes.ok) throw new Error(`Render start: ${await startRes.text()}`);

      const { job_id } = await startRes.json() as { job_id: string };
      updateJob(clipFilename, { jobId: job_id, status: 'rendering', progress: 25, message: 'Rendu en cours…' });

      // 4. Polling
      await pollUntilDone(clipFilename, job_id);

    } catch (e) {
      updateJob(clipFilename, { status: 'error', message: String(e), error: String(e) });
    }
  }

  async function pollUntilDone(clipFilename: string, jobId: string) {
    return new Promise<void>((resolve, reject) => {
      const interval = setInterval(async () => {
        try {
          const res = await fetch(`${BASE_URL}/render/status/${jobId}`);
          if (!res.ok) return;
          const data = await res.json() as {
            status: string; progress_pct: number; error?: string;
          };

          if (data.status === 'processing' || data.status === 'queued') {
            updateJob(clipFilename, {
              progress: 25 + Math.round(data.progress_pct * 0.6),
              message: `Rendu FFmpeg… ${Math.round(data.progress_pct)}%`,
            });
          } else if (data.status === 'done') {
            clearInterval(interval);
            delete pollingRefs.current[jobId];
            updateJob(clipFilename, { progress: 90, message: 'Téléchargement…', status: 'downloading' });
            await downloadAndSave(clipFilename, jobId);
            resolve();
          } else if (data.status === 'error') {
            clearInterval(interval);
            delete pollingRefs.current[jobId];
            updateJob(clipFilename, { status: 'error', message: data.error ?? 'Erreur rendu', error: data.error });
            reject(new Error(data.error));
          }
        } catch (_) {}
      }, 2000);

      pollingRefs.current[jobId] = interval;
    });
  }

  async function downloadAndSave(clipFilename: string, jobId: string) {
    const downloadUrl = `${BASE_URL}/render/download/${jobId}`;
    updateJob(clipFilename, {
      status: 'done',
      progress: 100,
      message: '✅ Rendu prêt — appuie sur le bouton pour télécharger',
      outputUri: downloadUrl,
    });
  }

  async function openDownloadInSafari(downloadUrl: string) {
    await Linking.openURL(downloadUrl);
  }

  // ── UI ──────────────────────────────────────────────────────────────────
  const statusColor: Record<JobStatus, string> = {
    idle: '#555', picking: '#f90', uploading: '#f90',
    rendering: '#4af', downloading: '#4af', saving: '#4af',
    done: '#4f4', error: '#f44',
  };

  const statusIcon: Record<JobStatus, string> = {
    idle: '⏳', picking: '📂', uploading: '⬆️',
    rendering: '🎬', downloading: '⬇️', saving: '💾',
    done: '✅', error: '❌',
  };

  if (!project) return null;

  const allDone = jobs.length > 0 && jobs.every(j => j.status === 'done');
  const anyRunning = jobs.some(j => !['idle', 'done', 'error'].includes(j.status));

  return (
    <ScrollView style={s.container} contentContainerStyle={{ padding: 16 }}>
      <StepNav current="step5-export" projectId={projectId as string} />
      <Text style={s.title}>Export vidéo</Text>

      {/* Résumé */}
      <View style={s.summary}>
        {project.activityName && (
          <Text style={s.summaryActivity}>{project.activityName}</Text>
        )}
        <Text style={s.summaryMeta}>
          {jobs.length} clip{jobs.length > 1 ? 's' : ''} · {project.widgetLayout?.length ?? 0} widget{(project.widgetLayout?.length ?? 0) > 1 ? 's' : ''}
        </Text>
        <Text style={s.infoNote}>
          💡 Comment accéder à tes vidéos :{'\n'}
          {'\n'}
          <Text style={s.bold}>Option 1 (direct)</Text> : dans le picker qui s'ouvre, touche "Photos" dans la barre latérale gauche.{'\n'}
          {'\n'}
          <Text style={s.bold}>Option 2</Text> : Ouvre Photos → vidéo → Partager → "Enregistrer dans Fichiers" → Sur mon iPhone. Puis reviens ici.
        </Text>
      </View>

      {/* Liste clips */}
      {jobs.map(job => (
        <View key={job.clipFilename} style={s.clipCard}>
          <View style={s.clipHeader}>
            <Text style={s.clipIcon}>{statusIcon[job.status]}</Text>
            <Text style={s.clipName} numberOfLines={1}>{job.clipFilename}</Text>
          </View>

          {/* Barre de progression */}
          <View style={s.progressBar}>
            <View style={[s.progressFill, {
              width: `${job.progress}%` as any,
              backgroundColor: statusColor[job.status],
            }]} />
          </View>

          <Text style={[s.clipMessage, { color: statusColor[job.status] }]}>
            {job.message}
          </Text>

          {/* Bouton action */}
          {job.status === 'idle' && (
            <TouchableOpacity style={s.renderBtn} onPress={() => renderClip(job.clipFilename)}>
              <Text style={s.renderBtnText}>🎬 Rendre ce clip</Text>
            </TouchableOpacity>
          )}
          {job.status === 'done' && job.outputUri && (
            <TouchableOpacity style={s.downloadBtn} onPress={() => openDownloadInSafari(job.outputUri!)}>
              <Text style={s.downloadBtnText}>⬇️ Télécharger dans Safari</Text>
            </TouchableOpacity>
          )}
          {job.status === 'error' && (
            <TouchableOpacity style={s.retryBtn} onPress={() => renderClip(job.clipFilename)}>
              <Text style={s.retryBtnText}>↩️ Réessayer</Text>
            </TouchableOpacity>
          )}
          {job.status === 'rendering' && job.jobId && (
            <TouchableOpacity style={s.copyBtn} onPress={() => {
              const url = `${BASE_URL}/render/download/${job.jobId}`;
              Clipboard.setString(url);
              Alert.alert('📋 Copié !', `Ouvre Safari et colle ce lien quand le rendu est fini :\n\n${url}`);
            }}>
              <Text style={s.copyBtnText}>📋 Copier lien de téléchargement</Text>
            </TouchableOpacity>
          )}
          {!['idle', 'done', 'error'].includes(job.status) && (
            <ActivityIndicator color={statusColor[job.status]} style={{ marginTop: 8 }} />
          )}
        </View>
      ))}

      {allDone && (
        <View style={s.successBox}>
          <Text style={s.successText}>
            🎉 Tous les clips ont été rendus et sauvegardés dans votre Pellicule !
          </Text>
        </View>
      )}

      {jobs.length === 0 && (
        <View style={s.emptyBox}>
          <Text style={s.emptyText}>
            Aucun clip synchronisé trouvé.{'\n'}
            Retournez à l'étape 2 pour ajouter des clips.
          </Text>
        </View>
      )}

      <View style={{ height: 40 }} />
    </ScrollView>
  );
}

const s = StyleSheet.create({
  container:       { flex: 1, backgroundColor: '#111' },
  title:           { color: '#fff', fontSize: 26, fontWeight: 'bold', marginBottom: 16 },
  summary:         { backgroundColor: '#1e1e1e', borderRadius: 12, padding: 16, marginBottom: 16 },
  summaryActivity: { color: '#4af', fontSize: 15, fontWeight: '600', marginBottom: 4 },
  summaryMeta:     { color: '#aaa', fontSize: 14, marginBottom: 8 },
  infoNote:        { color: '#666', fontSize: 12, lineHeight: 18 },
  bold:            { fontWeight: '700', color: '#888' },
  clipCard:        { backgroundColor: '#1e1e1e', borderRadius: 12, padding: 16, marginBottom: 12 },
  clipHeader:      { flexDirection: 'row', alignItems: 'center', marginBottom: 10 },
  clipIcon:        { fontSize: 20, marginRight: 10 },
  clipName:        { color: '#fff', fontSize: 14, fontWeight: '500', flex: 1 },
  progressBar:     { height: 4, backgroundColor: '#333', borderRadius: 2, overflow: 'hidden', marginBottom: 8 },
  progressFill:    { height: '100%', borderRadius: 2 },
  clipMessage:     { fontSize: 12, marginBottom: 8 },
  renderBtn:       { backgroundColor: '#0a2a3a', borderRadius: 10, padding: 12, alignItems: 'center', borderWidth: 1, borderColor: '#4af' },
  renderBtnText:   { color: '#4af', fontSize: 14, fontWeight: '600' },
  retryBtn:        { backgroundColor: '#2a1a1a', borderRadius: 10, padding: 12, alignItems: 'center', borderWidth: 1, borderColor: '#f44' },
  retryBtnText:    { color: '#f44', fontSize: 14, fontWeight: '600' },
  downloadBtn:     { backgroundColor: '#0a2a1a', borderRadius: 10, padding: 12, alignItems: 'center', borderWidth: 1, borderColor: '#4f4', marginTop: 8 },
  downloadBtnText: { color: '#4f4', fontSize: 14, fontWeight: '600' },
  copyBtn:         { backgroundColor: '#1a1a2a', borderRadius: 10, padding: 10, alignItems: 'center', borderWidth: 1, borderColor: '#66f', marginTop: 6 },
  copyBtnText:     { color: '#99f', fontSize: 13 },
  successBox:      { backgroundColor: '#0a2a0a', borderRadius: 12, padding: 16, alignItems: 'center', borderWidth: 1, borderColor: '#4f4' },
  successText:     { color: '#4f4', fontSize: 15, textAlign: 'center', lineHeight: 22 },
  emptyBox:        { padding: 32, alignItems: 'center' },
  emptyText:       { color: '#555', textAlign: 'center', lineHeight: 24 },
});
