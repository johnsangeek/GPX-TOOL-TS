import { useState } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, ActivityIndicator,
  Alert, ScrollView, FlatList,
} from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import * as DocumentPicker from 'expo-document-picker';
import * as Linking from 'expo-linking';
import { loadProjects, saveProject } from '../../lib/store';
import { uploadGpxFile } from '../../lib/api';
import { Project, ActivitySummary } from '../../types';
import StepNav from '../../components/StepNav';

const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1';

interface StravaActivity {
  id: number;
  name: string;
  type: string;
  date: string;
  distance_km: number;
  duration_s: number;
  elevation_m: number;
  avg_hr?: number;
}

interface StravaAthlete {
  id: number;
  firstname: string;
  lastname: string;
  city: string;
}

export default function Step1Screen() {
  const router = useRouter();
  const { projectId } = useLocalSearchParams<{ projectId: string }>();

  const [loading, setLoading]         = useState(false);
  const [status, setStatus]           = useState('');
  const [stravaToken, setStravaToken] = useState<string | null>(null);
  const [athlete, setAthlete]         = useState<StravaAthlete | null>(null);
  const [activities, setActivities]   = useState<StravaActivity[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [page, setPage]               = useState(1);

  async function getProject(): Promise<Project | null> {
    const projects = await loadProjects();
    return projects.find(p => p.id === projectId) ?? null;
  }

  // ── Strava OAuth (polling) ────────────────────────────────────────────────────
  async function connectStrava() {
    setLoading(true);
    setStatus('Connexion à Strava...');
    try {
      const callbackBase = BASE_URL.replace('/api/v1', '');
      const res = await fetch(`${BASE_URL}/strava/start?callback_base=${encodeURIComponent(callbackBase)}`);
      if (!res.ok) throw new Error(`API ${res.status}`);
      const { auth_url, state } = await res.json() as { auth_url: string; state: string };

      // Ouvre Strava dans Safari — l'utilisateur autorise puis revient dans l'app
      await Linking.openURL(auth_url);

      setStatus('En attente de l\'autorisation Strava...');

      // Polling toutes les 2s jusqu'à status=done (max 2 min)
      let attempts = 0;
      while (attempts < 60) {
        await new Promise(r => setTimeout(r, 2000));
        attempts++;
        const pollRes = await fetch(`${BASE_URL}/strava/poll/${state}`);
        if (!pollRes.ok) continue;
        const pollData = await pollRes.json() as { status: string; access_token?: string; athlete?: StravaAthlete; error?: string };

        if (pollData.status === 'error') throw new Error(pollData.error ?? 'Erreur Strava');
        if (pollData.status === 'done' && pollData.access_token && pollData.athlete) {
          setStravaToken(pollData.access_token);
          setAthlete(pollData.athlete);
          setStatus('Chargement des activités...');
          await loadActivities(pollData.access_token, 1);
          return;
        }
      }
      throw new Error('Timeout — autorisation non reçue en 2 minutes');
    } catch (e: unknown) {
      Alert.alert('Erreur Strava', String(e));
    } finally {
      setLoading(false);
      setStatus('');
    }
  }

  async function loadActivities(token: string, p: number) {
    const params = new URLSearchParams({ access_token: token, page: String(p), per_page: '20' });
    const res = await fetch(`${BASE_URL}/strava/activities?${params}`);
    if (!res.ok) throw new Error(`API ${res.status}`);
    const data = await res.json() as { activities: StravaActivity[] };
    setActivities(prev => p === 1 ? data.activities : [...prev, ...data.activities]);
    setPage(p);
  }

  async function loadMore() {
    if (!stravaToken || loadingMore) return;
    setLoadingMore(true);
    try { await loadActivities(stravaToken, page + 1); }
    finally { setLoadingMore(false); }
  }

  async function selectStravaActivity(activity: StravaActivity) {
    if (!stravaToken) return;
    setLoading(true);
    setStatus(`Import "${activity.name}"...`);
    try {
      const params = new URLSearchParams({ access_token: stravaToken });
      const res = await fetch(`${BASE_URL}/strava/activity/${activity.id}/import?${params}`, { method: 'POST' });
      if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
      const data = await res.json() as {
        session_id: string;
        activity_summary: ActivitySummary;
      };

      const project = await getProject();
      if (!project) return;
      await saveProject({
        ...project,
        sessionId:       data.session_id,
        gpxSource:       'strava',
        activityName:    activity.name,
        activityDate:    activity.date,
        activitySummary: data.activity_summary,
      });
      router.push({ pathname: '/(project)/step2-videos', params: { projectId } });
    } catch (e: unknown) {
      Alert.alert('Erreur import', String(e));
    } finally {
      setLoading(false);
      setStatus('');
    }
  }

  // ── GPX test local (dev) ──────────────────────────────────────────────────────
  async function loadTestGpx() {
    setLoading(true);
    setStatus('Chargement GPX test...');
    try {
      const res = await fetch(`${BASE_URL}/gpx/load-test`, { method: 'POST' });
      if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
      const data = await res.json() as {
        session_id: string;
        activity_summary: ActivitySummary;
      };
      const project = await getProject();
      if (!project) return;
      await saveProject({
        ...project,
        sessionId:       data.session_id,
        gpxSource:       'file',
        activitySummary: data.activity_summary,
      });
      router.push({ pathname: '/(project)/step2-videos', params: { projectId } });
    } catch (e: unknown) {
      Alert.alert('Erreur GPX test', String(e));
    } finally {
      setLoading(false);
      setStatus('');
    }
  }

  // ── Import fichier GPX ────────────────────────────────────────────────────────
  async function importGpxFile() {
    let asset: DocumentPicker.DocumentPickerAsset;
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: ['application/gpx+xml', 'application/octet-stream', '*/*'],
        copyToCacheDirectory: true,
      });
      if (result.canceled || !result.assets?.[0]) return;
      asset = result.assets[0];
    } catch (e: unknown) {
      Alert.alert('Erreur', `Impossible d'ouvrir le fichier : ${String(e)}`);
      return;
    }

    if (!asset.name.toLowerCase().endsWith('.gpx')) {
      Alert.alert('Fichier invalide', 'Sélectionnez un fichier .gpx');
      return;
    }

    setLoading(true);
    setStatus('Upload GPX en cours...');
    try {
      const data = await uploadGpxFile(asset.uri, asset.name) as {
        session_id: string;
        activity_summary: ActivitySummary;
        point_count: number;
        gpx_start: string;
        gpx_end: string;
      };

      const project = await getProject();
      if (!project) return;
      await saveProject({
        ...project,
        sessionId:       data.session_id,
        gpxSource:       'file',
        activitySummary: data.activity_summary,
      });
      router.push({ pathname: '/(project)/step2-videos', params: { projectId } });
    } catch (e: unknown) {
      Alert.alert('Erreur upload GPX', String(e));
    } finally {
      setLoading(false);
      setStatus('');
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────────
  function formatDuration(totalS: number) {
    const h = Math.floor(totalS / 3600);
    const m = Math.floor((totalS % 3600) / 60);
    return h > 0 ? `${h}h${m.toString().padStart(2, '0')}` : `${m}min`;
  }

  function formatDate(iso: string) {
    return new Date(iso).toLocaleDateString('fr-FR', {
      weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
    });
  }

  function activityIcon(type: string) {
    const icons: Record<string, string> = {
      Run: '🏃', Ride: '🚴', Hike: '🥾', Walk: '🚶',
      TrailRun: '🏔️', Swim: '🏊', NordicSki: '⛷️', AlpineSki: '🎿',
    };
    return icons[type] ?? '🏅';
  }

  // ── Vue Strava : liste activités ──────────────────────────────────────────────
  if (stravaToken && athlete) {
    return (
      <View style={s.container}>
        <View style={s.athleteBar}>
          <View style={{ flex: 1 }}>
            <Text style={s.athleteName}>{athlete.firstname} {athlete.lastname}</Text>
            {athlete.city ? <Text style={s.athleteCity}>{athlete.city}</Text> : null}
          </View>
          <TouchableOpacity onPress={() => { setStravaToken(null); setAthlete(null); setActivities([]); }}>
            <Text style={s.disconnectBtn}>Déconnexion</Text>
          </TouchableOpacity>
        </View>

        <Text style={s.listTitle}>Choisissez votre activité</Text>

        <FlatList
          data={activities}
          keyExtractor={a => String(a.id)}
          contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 32 }}
          renderItem={({ item }) => (
            <TouchableOpacity style={s.activityCard} onPress={() => selectStravaActivity(item)}>
              <Text style={s.activityIcon}>{activityIcon(item.type)}</Text>
              <View style={{ flex: 1 }}>
                <Text style={s.activityName} numberOfLines={1}>{item.name}</Text>
                <Text style={s.activityDate}>{formatDate(item.date)}</Text>
                <Text style={s.activityMeta}>
                  {item.distance_km.toFixed(1)} km · {formatDuration(item.duration_s)}
                  {item.elevation_m > 0 ? ` · D+ ${item.elevation_m.toFixed(0)}m` : ''}
                  {item.avg_hr ? ` · ❤️ ${item.avg_hr.toFixed(0)}` : ''}
                </Text>
              </View>
              <Text style={s.chevron}>›</Text>
            </TouchableOpacity>
          )}
          onEndReached={loadMore}
          onEndReachedThreshold={0.3}
          ListFooterComponent={loadingMore ? <ActivityIndicator color="#4af" style={{ margin: 16 }} /> : null}
        />

        {loading && (
          <View style={s.overlay}>
            <ActivityIndicator color="#4af" size="large" />
            <Text style={s.loadingText}>{status}</Text>
          </View>
        )}
      </View>
    );
  }

  // ── Vue principale ────────────────────────────────────────────────────────────
  return (
    <ScrollView style={s.container} contentContainerStyle={{ padding: 24 }}>
      <StepNav current="step1-source" projectId={projectId as string} />

      <Text style={s.title}>Source GPX</Text>
      <Text style={s.subtitle}>Choisissez comment importer les données de votre activité.</Text>

      <TouchableOpacity style={[s.card, { backgroundColor: '#2a1a0a' }]} onPress={connectStrava} disabled={loading}>
        <Text style={s.cardIcon}>🟠</Text>
        <View style={{ flex: 1 }}>
          <Text style={s.cardTitle}>Connecter Strava</Text>
          <Text style={s.cardDesc}>Toutes vos activités. GPS + FC + cadence + puissance.</Text>
        </View>
        <Text style={s.chevron}>›</Text>
      </TouchableOpacity>

      <TouchableOpacity style={[s.card, { backgroundColor: '#0a1a2a' }]} onPress={importGpxFile} disabled={loading}>
        <Text style={s.cardIcon}>📂</Text>
        <View style={{ flex: 1 }}>
          <Text style={s.cardTitle}>Importer un fichier .gpx</Text>
          <Text style={s.cardDesc}>Garmin Connect, Wahoo, Suunto, export Strava...</Text>
        </View>
        <Text style={s.chevron}>›</Text>
      </TouchableOpacity>

      <TouchableOpacity style={[s.card, { backgroundColor: '#0a2a0a' }]} onPress={loadTestGpx} disabled={loading}>
        <Text style={s.cardIcon}>🧪</Text>
        <View style={{ flex: 1 }}>
          <Text style={s.cardTitle}>GPX test — Rainy Run</Text>
          <Text style={s.cardDesc}>Charge directement le GPX local sur le serveur (dev only)</Text>
        </View>
        <Text style={s.chevron}>›</Text>
      </TouchableOpacity>

      {loading && (
        <View style={s.loading}>
          <ActivityIndicator color="#4af" size="large" />
          <Text style={s.loadingText}>{status}</Text>
        </View>
      )}

      <View style={s.infoBox}>
        <Text style={s.infoTitle}>ℹ️  Données supportées</Text>
        <Text style={s.infoText}>
          GPS, altitude, fréquence cardiaque, cadence, puissance, température.{'\n\n'}
          La connexion Strava est sécurisée — votre mot de passe n'est jamais transmis à cette app.
        </Text>
      </View>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  container:    { flex: 1, backgroundColor: '#111' },
  title:        { color: '#fff', fontSize: 26, fontWeight: 'bold', marginBottom: 8 },
  subtitle:     { color: '#888', fontSize: 15, marginBottom: 28, lineHeight: 22 },
  card:         { flexDirection: 'row', borderRadius: 14, padding: 18, marginBottom: 14, alignItems: 'center' },
  cardIcon:     { fontSize: 28, marginRight: 14 },
  cardTitle:    { color: '#fff', fontSize: 16, fontWeight: '600', marginBottom: 4 },
  cardDesc:     { color: '#888', fontSize: 13, lineHeight: 18 },
  chevron:      { color: '#444', fontSize: 24 },
  loading:      { alignItems: 'center', padding: 32 },
  loadingText:  { color: '#888', marginTop: 12, fontSize: 14 },
  infoBox:      { backgroundColor: '#1a1a1a', borderRadius: 12, padding: 16, marginTop: 12 },
  infoTitle:    { color: '#aaa', fontSize: 14, fontWeight: '600', marginBottom: 8 },
  infoText:     { color: '#666', fontSize: 13, lineHeight: 20 },
  // Strava list
  athleteBar:   { flexDirection: 'row', alignItems: 'center', backgroundColor: '#1e1e1e', padding: 16 },
  athleteName:  { color: '#fff', fontSize: 16, fontWeight: '600' },
  athleteCity:  { color: '#666', fontSize: 13, marginTop: 2 },
  disconnectBtn:{ color: '#f44', fontSize: 13 },
  listTitle:    { color: '#aaa', fontSize: 13, fontWeight: '600', textTransform: 'uppercase', letterSpacing: 0.5, paddingHorizontal: 16, paddingVertical: 10 },
  activityCard: { flexDirection: 'row', backgroundColor: '#1e1e1e', borderRadius: 12, padding: 14, marginBottom: 8, alignItems: 'center' },
  activityIcon: { fontSize: 24, marginRight: 12 },
  activityName: { color: '#fff', fontSize: 15, fontWeight: '500', marginBottom: 3 },
  activityDate: { color: '#4af', fontSize: 12, marginBottom: 3 },
  activityMeta: { color: '#666', fontSize: 12 },
  overlay:      { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: '#000a', alignItems: 'center', justifyContent: 'center' },
});
