/**
 * STEP 4b — Placement des widgets
 * Canvas 9:16 vertical avec drag & drop.
 * Bouton "Aperçu rendu" → appel backend → PNG base64 affiché.
 */

import { useState, useCallback, useRef } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, PanResponder,
  Image, ActivityIndicator, Alert, Dimensions, Modal,
} from 'react-native';
import { useRouter, useLocalSearchParams, useFocusEffect } from 'expo-router';
import { loadProjects, saveProject } from '../../lib/store';
import { Project, WidgetPlacement } from '../../types';
import StepNav from '../../components/StepNav';

const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000/api/v1';
const SCREEN_W = Dimensions.get('window').width;
const CANVAS_W = SCREEN_W - 32;
const CANVAS_H = Math.round(CANVAS_W * (16 / 9));

const WIDGET_LABELS: Record<string, string> = {
  speed: '⚡ Vitesse', pace: '🏃 Allure', hr: '❤️ FC',
  slope: '📐 Pente', elevation: '⛰️ Alt.', elev_gain: '⬆️ D+',
  distance: '📏 Dist.', time_elapsed: '⏱️ Temps', cadence: '🦵 Cadence',
  power: '⚡ Watts', temperature: '🌡️ Temp.', bearing: '🧭 Cap',
  coords: '📍 GPS', elev_profile: '📈 Profil',
};

const WIDGET_COLORS: Record<string, string> = {
  speed: '#4af', pace: '#4af', hr: '#f44', slope: '#fa0',
  elevation: '#0f8', elev_gain: '#0f8', distance: '#aaf',
  time_elapsed: '#aaf', cadence: '#f8a', power: '#ff0',
  temperature: '#0cf', bearing: '#f0f', coords: '#888', elev_profile: '#888',
};

// Taille du badge widget sur le canvas (en pixels)
const BADGE_W = 90;
const BADGE_H = 44;

export default function Step4LayoutScreen() {
  const router = useRouter();
  const { projectId } = useLocalSearchParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);

  // positions en pixels absolus sur le canvas
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>({});
  const [widgetKeys, setWidgetKeys] = useState<string[]>([]);

  const [loading, setLoading] = useState(false);
  const [previewUri, setPreviewUri] = useState<string | null>(null);
  const [showPreview, setShowPreview] = useState(false);

  useFocusEffect(
    useCallback(() => {
      loadProjects().then(projects => {
        const p = projects.find(x => x.id === projectId) ?? null;
        setProject(p);
        if (p?.widgetLayout) {
          const keys = p.widgetLayout.map(w => w.key);
          setWidgetKeys(keys);
          // Convertit les positions 0..1 en pixels canvas
          const px: Record<string, { x: number; y: number }> = {};
          p.widgetLayout.forEach(w => {
            px[w.key] = {
              x: Math.round(w.x * CANVAS_W),
              y: Math.round(w.y * CANVAS_H),
            };
          });
          setPositions(px);
        }
      });
    }, [projectId])
  );

  // ── Drag & drop ────────────────────────────────────────────────────────────
  // Stocke les positions de départ pour chaque widget au moment du grab
  const dragStartPos = useRef<Record<string, { x: number; y: number }>>({});

  // Pré-créé les PanResponders (un par widget, stable entre renders)
  const panResponders = useRef<Record<string, ReturnType<typeof PanResponder.create>>>({});

  function getPanResponder(key: string) {
    if (!panResponders.current[key]) {
      panResponders.current[key] = PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onPanResponderGrant: () => {
          // Capture la position courante au moment du toucher
          setPositions(prev => {
            dragStartPos.current[key] = prev[key] ?? { x: 20, y: 20 };
            return prev;
          });
        },
        onPanResponderMove: (_, gs) => {
          const start = dragStartPos.current[key] ?? { x: 20, y: 20 };
          const nx = Math.max(0, Math.min(CANVAS_W - BADGE_W, start.x + gs.dx));
          const ny = Math.max(0, Math.min(CANVAS_H - BADGE_H, start.y + gs.dy));
          setPositions(prev => ({ ...prev, [key]: { x: nx, y: ny } }));
        },
        onPanResponderRelease: () => {},
      });
    }
    return panResponders.current[key];
  }

  // ── Aperçu rendu backend ────────────────────────────────────────────────
  async function renderPreview() {
    if (!project?.sessionId) return;
    setLoading(true);
    try {
      const layout = widgetKeys.map(key => {
        const pos = positions[key] ?? { x: 20, y: 20 };
        return {
          key,
          x: parseFloat((pos.x / CANVAS_W).toFixed(4)),
          y: parseFloat((pos.y / CANVAS_H).toFixed(4)),
          anchor: 'top-left',
        };
      });

      const res = await fetch(`${BASE_URL}/render/preview-frame`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: project.sessionId,
          widget_layout: layout,
          frame_time_s: 5.0,
          canvas_width: CANVAS_W,
          canvas_height: CANVAS_H,
        }),
      });

      if (!res.ok) {
        const err = await res.text();
        Alert.alert('Erreur preview', err);
        return;
      }

      const data = await res.json() as { image_b64: string };
      setPreviewUri(`data:image/jpeg;base64,${data.image_b64}`);
      setShowPreview(true);
    } catch (e) {
      Alert.alert('Erreur', String(e));
    } finally {
      setLoading(false);
    }
  }

  // ── Sauvegarder et continuer ────────────────────────────────────────────
  async function saveAndNext() {
    if (!project) return;
    const layout: WidgetPlacement[] = widgetKeys.map(key => {
      const pos = positions[key] ?? { x: 20, y: 20 };
      return {
        key,
        x: parseFloat((pos.x / CANVAS_W).toFixed(4)),
        y: parseFloat((pos.y / CANVAS_H).toFixed(4)),
      };
    });
    await saveProject({ ...project, widgetLayout: layout });
    router.push({ pathname: '/(project)/step5-export', params: { projectId } });
  }

  return (
    <View style={s.container}>
      <StepNav current="step4-layout" projectId={projectId as string} />
      <View style={s.header}>
        <Text style={s.title}>Placer les widgets</Text>
        <Text style={s.sub}>Glissez pour repositionner</Text>
      </View>

      {/* Canvas */}
      <View style={s.canvasWrap}>
        <View style={[s.canvas, { width: CANVAS_W, height: CANVAS_H }]}>
          {/* Fond simulé */}
          <View style={s.canvasBg} />

          {/* Grid helpers */}
          <View style={[s.gridLine, { top: CANVAS_H / 3 }]} />
          <View style={[s.gridLine, { top: (CANVAS_H * 2) / 3 }]} />

          {/* Widgets draggables */}
          {widgetKeys.map(key => {
            const pos = positions[key] ?? { x: 20, y: 20 + widgetKeys.indexOf(key) * 60 };
            const pr = getPanResponder(key);
            const color = WIDGET_COLORS[key] ?? '#888';
            return pr ? (
              <View
                key={key}
                style={[s.badge, {
                  left: pos.x, top: pos.y,
                  borderColor: color,
                  width: BADGE_W,
                  height: BADGE_H,
                }]}
                {...pr.panHandlers}
              >
                <Text style={[s.badgeText, { color }]} numberOfLines={1}>
                  {WIDGET_LABELS[key] ?? key}
                </Text>
              </View>
            ) : null;
          })}
        </View>
      </View>

      {/* Boutons */}
      <View style={s.footer}>
        <TouchableOpacity
          style={[s.previewBtn, loading && { opacity: 0.5 }]}
          onPress={renderPreview}
          disabled={loading}
        >
          {loading
            ? <ActivityIndicator color="#fff" />
            : <Text style={s.previewBtnText}>🎬 Aperçu rendu frame 1</Text>
          }
        </TouchableOpacity>

        <TouchableOpacity style={s.nextBtn} onPress={saveAndNext}>
          <Text style={s.nextBtnText}>Valider le layout →</Text>
        </TouchableOpacity>
      </View>

      {/* Modal preview */}
      <Modal visible={showPreview} transparent animationType="fade">
        <View style={s.modalOverlay}>
          <View style={s.modalBox}>
            <Text style={s.modalTitle}>Aperçu frame 1</Text>
            {previewUri && (
              <Image
                source={{ uri: previewUri }}
                style={{ width: CANVAS_W, height: CANVAS_H, borderRadius: 12 }}
                resizeMode="contain"
              />
            )}
            <TouchableOpacity style={s.modalClose} onPress={() => setShowPreview(false)}>
              <Text style={s.modalCloseText}>Fermer</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const s = StyleSheet.create({
  container:    { flex: 1, backgroundColor: '#111' },
  header:       { padding: 16, paddingBottom: 8 },
  title:        { color: '#fff', fontSize: 20, fontWeight: 'bold' },
  sub:          { color: '#666', fontSize: 13, marginTop: 2 },
  canvasWrap:   { alignItems: 'center', paddingHorizontal: 16 },
  canvas:       { borderRadius: 12, overflow: 'hidden', position: 'relative', borderWidth: 1, borderColor: '#333' },
  canvasBg:     { ...StyleSheet.absoluteFillObject, backgroundColor: '#1a1a2e' },
  gridLine:     { position: 'absolute', left: 0, right: 0, height: 1, backgroundColor: '#ffffff10' },
  badge: {
    position: 'absolute',
    borderRadius: 8,
    borderWidth: 1.5,
    backgroundColor: '#00000088',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 8,
  },
  badgeText:    { fontSize: 12, fontWeight: '600' },
  footer:       { position: 'absolute', bottom: 0, left: 0, right: 0, padding: 16, paddingBottom: 32, gap: 10 },
  previewBtn:   { backgroundColor: '#1e3a1e', borderRadius: 14, padding: 14, alignItems: 'center', borderWidth: 1, borderColor: '#4f4' },
  previewBtnText:{ color: '#4f4', fontSize: 15, fontWeight: '600' },
  nextBtn:      { backgroundColor: '#4af', borderRadius: 14, padding: 16, alignItems: 'center' },
  nextBtnText:  { color: '#fff', fontSize: 17, fontWeight: 'bold' },
  modalOverlay: { flex: 1, backgroundColor: '#000d', alignItems: 'center', justifyContent: 'center' },
  modalBox:     { backgroundColor: '#111', borderRadius: 16, padding: 16, alignItems: 'center', gap: 12 },
  modalTitle:   { color: '#fff', fontSize: 16, fontWeight: 'bold' },
  modalClose:   { backgroundColor: '#333', borderRadius: 10, paddingHorizontal: 24, paddingVertical: 10 },
  modalCloseText:{ color: '#fff', fontSize: 15 },
});
