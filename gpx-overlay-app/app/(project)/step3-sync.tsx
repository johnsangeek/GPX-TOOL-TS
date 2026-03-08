/**
 * STEP 3 — Vérification Sync + Calibration
 * Affiche le résultat de sync pour chaque clip.
 * Permet d'ajuster l'offset (slider ±90s) ou de lancer la calibration clip km.
 */

import { useState, useCallback } from 'react';
import {
  View, Text, StyleSheet, ScrollView, TouchableOpacity, Alert,
} from 'react-native';
import { useRouter, useLocalSearchParams, useFocusEffect } from 'expo-router';
import { loadProjects, saveProject } from '../../lib/store';
import { adjustOffset } from '../../lib/api';
import { Project, Clip } from '../../types';
import StepNav from '../../components/StepNav';

export default function Step3Screen() {
  const router = useRouter();
  const { projectId } = useLocalSearchParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [offset, setOffset] = useState(0);
  const [adjusting, setAdjusting] = useState(false);

  useFocusEffect(
    useCallback(() => {
      loadProjects().then(projects => {
        const p = projects.find(x => x.id === projectId) ?? null;
        setProject(p);
        setOffset(p?.syncResult?.globalOffsetS ?? 0);
      });
    }, [projectId])
  );

  function coverageColor(pct?: number) {
    if (!pct) return '#555';
    if (pct > 70) return '#4f4';
    if (pct > 20) return '#f90';
    return '#f44';
  }

  function statusIcon(clip: Clip) {
    if (clip.syncStatus === 'ok')      return '✅';
    if (clip.syncStatus === 'warning') return '⚠️';
    if (clip.syncStatus === 'error')   return '❌';
    return '⏳';
  }

  async function applyOffset(newOffset: number) {
    if (!project?.syncResult) return;
    setAdjusting(true);
    try {
      // On applique sur le premier clip qui a de la data pour tester
      const firstClip = project.clips?.find(c => c.syncStatus !== 'error');
      if (firstClip) {
        await adjustOffset(project.syncResult.sessionId, firstClip.filename, newOffset);
      }
      const updated: Project = {
        ...project,
        offsetS: newOffset,
        syncResult: { ...project.syncResult, globalOffsetS: newOffset },
      };
      await saveProject(updated);
      setProject(updated);
    } catch (e: unknown) {
      Alert.alert('Erreur', String(e));
    } finally {
      setAdjusting(false);
    }
  }

  function goNext() {
    router.push({ pathname: '/(project)/step4-widgets', params: { projectId } });
  }

  if (!project) return null;

  const okCount   = project.clips?.filter(c => c.syncStatus === 'ok').length ?? 0;
  const warnCount = project.clips?.filter(c => c.syncStatus === 'warning').length ?? 0;
  const errCount  = project.clips?.filter(c => c.syncStatus === 'error').length ?? 0;

  return (
    <ScrollView style={s.container} contentContainerStyle={{ padding: 16 }}>
      <StepNav current="step3-sync" projectId={projectId as string} />

      {/* Résumé global */}
      <View style={s.summary}>
        <Text style={s.summaryTitle}>Résultat de synchronisation</Text>
        <View style={s.summaryRow}>
          {okCount   > 0 && <Text style={s.ok}>✅ {okCount} clip{okCount>1?'s':''} OK</Text>}
          {warnCount > 0 && <Text style={s.warn}>⚠️  {warnCount} partiel{warnCount>1?'s':''}</Text>}
          {errCount  > 0 && <Text style={s.err}>❌ {errCount} hors plage</Text>}
        </View>
        {errCount > 0 && (
          <Text style={s.errHint}>
            Les clips ❌ ne correspondent pas à la période de votre GPX. Vérifiez qu'ils appartiennent bien à cette course.
          </Text>
        )}
      </View>

      {/* Liste des clips */}
      {project.clips?.map(clip => (
        <View key={clip.filename} style={s.clipCard}>
          <View style={s.clipHeader}>
            <Text style={s.clipIcon}>{statusIcon(clip)}</Text>
            <Text style={s.clipName} numberOfLines={1}>{clip.filename}</Text>
          </View>
          {clip.coveragePct !== undefined && (
            <View style={s.coverageRow}>
              <Text style={s.coverageLabel}>Couverture GPX</Text>
              <Text style={[s.coverageValue, { color: coverageColor(clip.coveragePct) }]}>
                {clip.coveragePct.toFixed(0)}%
              </Text>
            </View>
          )}
          {clip.syncError && (
            <Text style={s.clipError}>{clip.syncError}</Text>
          )}
        </View>
      ))}

      {/* Calibration — Slider offset */}
      <View style={s.section}>
        <Text style={s.sectionTitle}>Calibration de l'offset</Text>
        <Text style={s.sectionDesc}>
          Si les widgets sont décalés dans le temps, ajustez l'offset. Un offset négatif signifie que le GPX est en avance sur la vidéo.
        </Text>

        {/* Mode auto */}
        <View style={s.modeRow}>
          <Text style={s.modeLabel}>Offset actuel</Text>
          <Text style={s.modeValue}>{offset > 0 ? '+' : ''}{offset.toFixed(1)}s</Text>
        </View>

        <View style={s.sliderRow}>
          <Text style={s.sliderLabel}>-90s</Text>
          <View style={{ flex: 1 }}>
            <Text style={s.sliderCurrent}>{offset.toFixed(1)}s</Text>
          </View>
          <Text style={s.sliderLabel}>+90s</Text>
        </View>

        {/* Slider — @react-native-community/slider requis */}
        <View style={s.sliderPlaceholder}>
          <TouchableOpacity style={s.sliderBtn} onPress={() => setOffset(o => Math.max(-90, o - 1))}>
            <Text style={s.sliderBtnText}>-1s</Text>
          </TouchableOpacity>
          <Text style={s.sliderValue}>{offset.toFixed(0)}s</Text>
          <TouchableOpacity style={s.sliderBtn} onPress={() => setOffset(o => Math.min(90, o + 1))}>
            <Text style={s.sliderBtnText}>+1s</Text>
          </TouchableOpacity>
          <TouchableOpacity style={s.sliderBtn} onPress={() => setOffset(o => Math.max(-90, o - 5))}>
            <Text style={s.sliderBtnText}>-5s</Text>
          </TouchableOpacity>
          <TouchableOpacity style={s.sliderBtn} onPress={() => setOffset(o => Math.min(90, o + 5))}>
            <Text style={s.sliderBtnText}>+5s</Text>
          </TouchableOpacity>
        </View>

        <TouchableOpacity
          style={[s.applyBtn, adjusting && s.applyBtnDisabled]}
          onPress={() => applyOffset(offset)}
          disabled={adjusting}
        >
          <Text style={s.applyBtnText}>
            {adjusting ? 'Application...' : 'Appliquer l\'offset'}
          </Text>
        </TouchableOpacity>

        <TouchableOpacity
          style={s.resetBtn}
          onPress={() => { setOffset(0); applyOffset(0); }}
        >
          <Text style={s.resetBtnText}>Réinitialiser (auto)</Text>
        </TouchableOpacity>
      </View>

      {/* Calibration clip km */}
      <View style={s.section}>
        <Text style={s.sectionTitle}>Calibration précise (optionnel)</Text>
        <Text style={s.sectionDesc}>
          Si vous avez filmé votre montre au moment où elle annonce un kilomètre, vous pouvez calculer l'offset exact pour toute la session.
        </Text>
        <TouchableOpacity
          style={s.calibrateBtn}
          onPress={() =>
            Alert.alert(
              'Calibration par clip km',
              'Fonctionnalité disponible dans la prochaine version.',
              [{ text: 'OK' }]
            )
          }
        >
          <Text style={s.calibrateBtnText}>🎯 Calibrer par clip km marker</Text>
        </TouchableOpacity>
      </View>

      {/* Warnings serveur */}
      {project.syncResult?.warnings?.length ? (
        <View style={s.warningBox}>
          {project.syncResult.warnings.map((w, i) => (
            <Text key={i} style={s.warningText}>⚠️  {w}</Text>
          ))}
        </View>
      ) : null}

      {/* Suivant */}
      <TouchableOpacity style={s.nextBtn} onPress={goNext}>
        <Text style={s.nextBtnText}>Choisir les widgets →</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  container:       { flex: 1, backgroundColor: '#111' },
  summary:         { backgroundColor: '#1e1e1e', borderRadius: 12, padding: 16, marginBottom: 14 },
  summaryTitle:    { color: '#fff', fontSize: 16, fontWeight: '600', marginBottom: 8 },
  summaryRow:      { flexDirection: 'row', gap: 12, flexWrap: 'wrap' },
  ok:              { color: '#4f4', fontSize: 14 },
  warn:            { color: '#f90', fontSize: 14 },
  err:             { color: '#f44', fontSize: 14 },
  errHint:         { color: '#f44', fontSize: 12, marginTop: 8, lineHeight: 18 },
  clipCard:        { backgroundColor: '#1e1e1e', borderRadius: 12, padding: 14, marginBottom: 10 },
  clipHeader:      { flexDirection: 'row', alignItems: 'center', marginBottom: 8 },
  clipIcon:        { fontSize: 16, marginRight: 8 },
  clipName:        { color: '#fff', fontSize: 14, flex: 1 },
  coverageRow:     { flexDirection: 'row', justifyContent: 'space-between' },
  coverageLabel:   { color: '#666', fontSize: 13 },
  coverageValue:   { fontSize: 13, fontWeight: '600' },
  clipError:       { color: '#f44', fontSize: 12, marginTop: 4 },
  section:         { backgroundColor: '#1e1e1e', borderRadius: 12, padding: 16, marginBottom: 14 },
  sectionTitle:    { color: '#fff', fontSize: 15, fontWeight: '600', marginBottom: 6 },
  sectionDesc:     { color: '#777', fontSize: 13, lineHeight: 18, marginBottom: 14 },
  modeRow:         { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 8 },
  modeLabel:       { color: '#aaa', fontSize: 14 },
  modeValue:       { color: '#4af', fontSize: 14, fontWeight: '600' },
  sliderRow:       { flexDirection: 'row', alignItems: 'center', marginBottom: 8 },
  sliderLabel:     { color: '#555', fontSize: 12, width: 30 },
  sliderCurrent:   { color: '#fff', fontSize: 16, fontWeight: 'bold', textAlign: 'center' },
  sliderPlaceholder: { flexDirection: 'row', gap: 8, justifyContent: 'center', alignItems: 'center', marginBottom: 14 },
  sliderBtn:       { backgroundColor: '#2a2a2a', borderRadius: 8, paddingHorizontal: 14, paddingVertical: 10 },
  sliderBtnText:   { color: '#4af', fontSize: 14, fontWeight: '600' },
  sliderValue:     { color: '#fff', fontSize: 20, fontWeight: 'bold', width: 60, textAlign: 'center' },
  applyBtn:        { backgroundColor: '#4af', borderRadius: 10, padding: 13, alignItems: 'center', marginBottom: 8 },
  applyBtnDisabled:{ opacity: 0.5 },
  applyBtnText:    { color: '#fff', fontSize: 15, fontWeight: '600' },
  resetBtn:        { alignItems: 'center', padding: 10 },
  resetBtnText:    { color: '#555', fontSize: 13 },
  calibrateBtn:    { backgroundColor: '#1a2a1a', borderRadius: 10, padding: 14, alignItems: 'center' },
  calibrateBtnText:{ color: '#4f4', fontSize: 15, fontWeight: '600' },
  warningBox:      { backgroundColor: '#2a1a00', borderRadius: 10, padding: 12, marginBottom: 14 },
  warningText:     { color: '#f90', fontSize: 13, lineHeight: 18 },
  nextBtn:         { backgroundColor: '#4af', borderRadius: 14, padding: 16, alignItems: 'center', marginBottom: 32 },
  nextBtnText:     { color: '#fff', fontSize: 17, fontWeight: 'bold' },
});
