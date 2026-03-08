/**
 * STEP 4 — Widget Board
 * Sélection des widgets par catégorie.
 * Placement basique pour l'instant (liste checkbox).
 * Le drag & drop et preview vidéo arrivent en v2.
 */

import { useState, useCallback, useEffect } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, SectionList,
} from 'react-native';
import { useRouter, useLocalSearchParams, useFocusEffect } from 'expo-router';
import { loadProjects, saveProject } from '../../lib/store';
import { getWidgets } from '../../lib/api';
import { Project, WidgetDef, WidgetPlacement } from '../../types';
import StepNav from '../../components/StepNav';

const FALLBACK_WIDGETS: WidgetDef[] = [
  { key: 'speed',       label: 'Vitesse',             unit: 'km/h',  description: 'Vitesse instantanée',           category: 'speed'   },
  { key: 'pace',        label: 'Allure',              unit: '/km',   description: 'Allure en min/km',              category: 'speed'   },
  { key: 'hr',          label: 'Fréquence cardiaque', unit: 'bpm',   description: 'FC avec zones couleur',         category: 'cardio'  },
  { key: 'slope',       label: 'Pente',               unit: '%',     description: 'Pente instantanée',             category: 'terrain' },
  { key: 'elevation',   label: 'Altitude',            unit: 'm',     description: 'Altitude GPS',                  category: 'terrain' },
  { key: 'elev_gain',   label: 'Dénivelé +',          unit: 'm',     description: 'D+ cumulé depuis le départ',    category: 'terrain' },
  { key: 'distance',    label: 'Distance',            unit: 'km',    description: 'Distance depuis le départ',     category: 'stats'   },
  { key: 'time_elapsed',label: 'Temps',               unit: 'mm:ss', description: 'Temps écoulé',                 category: 'stats'   },
  { key: 'cadence',     label: 'Cadence',             unit: 'spm',   description: 'Foulées par minute',           category: 'stats'   },
  { key: 'power',       label: 'Puissance',           unit: 'W',     description: 'Puissance (si capteur)',        category: 'stats'   },
  { key: 'temperature', label: 'Température',         unit: '°C',    description: 'Température (si capteur)',      category: 'stats'   },
  { key: 'bearing',     label: 'Cap',                 unit: '',      description: 'Direction (N/NE/E...)',         category: 'gps'     },
  { key: 'coords',      label: 'Coordonnées GPS',     unit: '°',     description: 'Lat/Lon',                      category: 'gps'     },
  { key: 'elev_profile',label: 'Profil altimétrique', unit: '',      description: 'Courbe de dénivelé SVG',        category: 'graph'   },
];

const CATEGORY_LABELS: Record<string, string> = {
  speed:   '⚡ Vitesse',
  cardio:  '❤️  Cardio',
  terrain: '⛰️  Terrain',
  stats:   '📊 Stats',
  gps:     '📍 GPS',
  graph:   '📈 Graphes',
};

export default function Step4Screen() {
  const router = useRouter();
  const { projectId } = useLocalSearchParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [widgets, setWidgets] = useState<WidgetDef[]>(FALLBACK_WIDGETS);
  const [selected, setSelected] = useState<Set<string>>(new Set(['speed', 'pace', 'hr', 'elevation']));

  useFocusEffect(
    useCallback(() => {
      loadProjects().then(projects => {
        const p = projects.find(x => x.id === projectId) ?? null;
        setProject(p);
        if (p?.widgetLayout) {
          setSelected(new Set(p.widgetLayout.map(w => w.key)));
        }
      });

      // Charge la liste depuis l'API (avec fallback sur les widgets hardcodés ci-dessus)
      getWidgets()
        .then((data: unknown) => {
          const d = data as { widgets: WidgetDef[] };
          if (d.widgets?.length) setWidgets(d.widgets);
        })
        .catch(() => {/* utilise FALLBACK_WIDGETS */});
    }, [projectId])
  );

  function toggleWidget(key: string) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function validate() {
    if (!project) return;
    if (selected.size === 0) return;

    // Layout par défaut : widgets empilés en haut à gauche
    const layout: WidgetPlacement[] = Array.from(selected).map((key, i) => ({
      key,
      x: 0.05,
      y: 0.05 + i * 0.12,
    }));

    const updated: Project = { ...project, widgetLayout: layout };
    await saveProject(updated);

    router.push({ pathname: '/(project)/step4-layout', params: { projectId } });
  }

  // Regroupement par catégorie pour SectionList
  const sections = Object.entries(CATEGORY_LABELS).map(([cat, label]) => ({
    title: label,
    data: widgets.filter(w => w.category === cat),
  })).filter(s => s.data.length > 0);

  return (
    <View style={s.container}>
      <StepNav current="step4-widgets" projectId={projectId as string} />
      <View style={s.header}>
        <Text style={s.headerTitle}>Choisissez vos widgets</Text>
        <Text style={s.headerSub}>{selected.size} sélectionné{selected.size > 1 ? 's' : ''}</Text>
      </View>

      <SectionList
        sections={sections}
        keyExtractor={item => item.key}
        contentContainerStyle={{ padding: 16 }}
        renderSectionHeader={({ section }) => (
          <Text style={s.sectionHeader}>{section.title}</Text>
        )}
        renderItem={({ item }) => {
          const isSelected = selected.has(item.key);
          return (
            <TouchableOpacity
              style={[s.widgetRow, isSelected && s.widgetRowSelected]}
              onPress={() => toggleWidget(item.key)}
            >
              <View style={[s.checkbox, isSelected && s.checkboxSelected]}>
                {isSelected && <Text style={s.checkmark}>✓</Text>}
              </View>
              <View style={s.widgetInfo}>
                <Text style={[s.widgetLabel, isSelected && s.widgetLabelSelected]}>
                  {item.label}
                </Text>
                <Text style={s.widgetDesc}>{item.description}</Text>
              </View>
              {item.unit ? (
                <Text style={s.widgetUnit}>{item.unit}</Text>
              ) : null}
            </TouchableOpacity>
          );
        }}
        ListFooterComponent={<View style={{ height: 100 }} />}
      />

      {/* Bouton valider */}
      <View style={s.footer}>
        <TouchableOpacity
          style={[s.validateBtn, selected.size === 0 && s.validateBtnDisabled]}
          onPress={validate}
          disabled={selected.size === 0}
        >
          <Text style={s.validateBtnText}>
            Placer {selected.size} widget{selected.size > 1 ? 's' : ''} →
          </Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  container:           { flex: 1, backgroundColor: '#111' },
  header:              { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', padding: 16, paddingBottom: 0 },
  headerTitle:         { color: '#fff', fontSize: 20, fontWeight: 'bold' },
  headerSub:           { color: '#4af', fontSize: 14 },
  sectionHeader:       { color: '#888', fontSize: 13, fontWeight: '600', marginTop: 16, marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.5 },
  widgetRow:           { flexDirection: 'row', alignItems: 'center', backgroundColor: '#1e1e1e', borderRadius: 10, padding: 14, marginBottom: 8 },
  widgetRowSelected:   { backgroundColor: '#0a2030', borderWidth: 1, borderColor: '#4af' },
  checkbox:            { width: 22, height: 22, borderRadius: 6, borderWidth: 2, borderColor: '#444', marginRight: 14, alignItems: 'center', justifyContent: 'center' },
  checkboxSelected:    { backgroundColor: '#4af', borderColor: '#4af' },
  checkmark:           { color: '#fff', fontSize: 13, fontWeight: 'bold' },
  widgetInfo:          { flex: 1 },
  widgetLabel:         { color: '#ccc', fontSize: 15, fontWeight: '500' },
  widgetLabelSelected: { color: '#fff' },
  widgetDesc:          { color: '#555', fontSize: 12, marginTop: 2 },
  widgetUnit:          { color: '#444', fontSize: 13, marginLeft: 8 },
  footer:              { position: 'absolute', bottom: 0, left: 0, right: 0, backgroundColor: '#111', padding: 16, paddingBottom: 32 },
  validateBtn:         { backgroundColor: '#4af', borderRadius: 14, padding: 16, alignItems: 'center' },
  validateBtnDisabled: { opacity: 0.4 },
  validateBtnText:     { color: '#fff', fontSize: 17, fontWeight: 'bold' },
});
