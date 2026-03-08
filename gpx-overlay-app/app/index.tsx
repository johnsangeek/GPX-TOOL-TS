/**
 * SCREEN 0 — Home
 * Liste des projets. Bouton "Nouveau projet".
 * Style CapCut : cards en liste, swipe pour supprimer.
 */

import { useCallback, useState } from 'react';
import {
  View, Text, FlatList, TouchableOpacity, Alert,
  StyleSheet, TextInput, Modal,
} from 'react-native';
import { useRouter } from 'expo-router';
import { useFocusEffect } from '@react-navigation/native';
import { Project } from '../types';
import { loadProjects, saveProject, deleteProject, newProject } from '../lib/store';

export default function HomeScreen() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [newName, setNewName] = useState('');

  // Recharge à chaque fois qu'on revient sur cet écran
  useFocusEffect(
    useCallback(() => {
      loadProjects().then(setProjects);
    }, [])
  );

  async function createProject() {
    if (!newName.trim()) return;
    const p = newProject(newName.trim());
    await saveProject(p);
    setShowModal(false);
    setNewName('');
    // Navigue directement vers step 1
    router.push({ pathname: '/(project)/step1-source', params: { projectId: p.id } });
  }

  async function confirmDelete(id: string, name: string) {
    Alert.alert('Supprimer', `Supprimer le projet "${name}" ?`, [
      { text: 'Annuler', style: 'cancel' },
      {
        text: 'Supprimer', style: 'destructive',
        onPress: async () => {
          await deleteProject(id);
          setProjects(prev => prev.filter(p => p.id !== id));
        },
      },
    ]);
  }

  function openProject(p: Project) {
    // Reprend là où on s'était arrêté
    if (!p.sessionId) {
      router.push({ pathname: '/(project)/step1-source', params: { projectId: p.id } });
    } else if (!p.syncResult) {
      router.push({ pathname: '/(project)/step2-videos', params: { projectId: p.id } });
    } else if (!p.widgetLayout) {
      router.push({ pathname: '/(project)/step4-widgets', params: { projectId: p.id } });
    } else {
      router.push({ pathname: '/(project)/step5-export', params: { projectId: p.id } });
    }
  }

  function stepLabel(p: Project): string {
    if (!p.sessionId)   return 'Étape 1 — Source GPX';
    if (!p.clips?.length) return 'Étape 2 — Vidéos';
    if (!p.syncResult)  return 'Étape 3 — Sync';
    if (!p.widgetLayout) return 'Étape 4 — Widgets';
    return 'Étape 5 — Export';
  }

  function formatDate(iso: string) {
    return new Date(iso).toLocaleDateString('fr-FR', {
      day: 'numeric', month: 'short', year: 'numeric',
    });
  }

  return (
    <View style={s.container}>
      {projects.length === 0 ? (
        <View style={s.empty}>
          <Text style={s.emptyIcon}>🏃</Text>
          <Text style={s.emptyTitle}>Aucun projet</Text>
          <Text style={s.emptyText}>
            Créez votre premier projet pour commencer à ajouter des overlays GPX sur vos vidéos.
          </Text>
        </View>
      ) : (
        <FlatList
          data={projects}
          keyExtractor={p => p.id}
          contentContainerStyle={{ paddingTop: 12 }}
          renderItem={({ item }) => (
            <TouchableOpacity style={s.card} onPress={() => openProject(item)}>
              <View style={s.cardLeft}>
                <Text style={s.cardName}>{item.name}</Text>
                {item.activityName && (
                  <Text style={s.cardActivity}>{item.activityName}</Text>
                )}
                <Text style={s.cardStep}>{stepLabel(item)}</Text>
                <Text style={s.cardDate}>{formatDate(item.createdAt)}</Text>
              </View>
              <TouchableOpacity
                style={s.deleteBtn}
                onPress={() => confirmDelete(item.id, item.name)}
              >
                <Text style={s.deleteBtnText}>✕</Text>
              </TouchableOpacity>
            </TouchableOpacity>
          )}
        />
      )}

      {/* Bouton + fixe en bas */}
      <TouchableOpacity style={s.fab} onPress={() => setShowModal(true)}>
        <Text style={s.fabText}>+ Nouveau projet</Text>
      </TouchableOpacity>

      {/* Modal nom du projet */}
      <Modal visible={showModal} transparent animationType="slide">
        <View style={s.modalOverlay}>
          <View style={s.modalBox}>
            <Text style={s.modalTitle}>Nouveau projet</Text>
            <TextInput
              style={s.input}
              placeholder="Ex: Central Park 4 déc"
              placeholderTextColor="#666"
              value={newName}
              onChangeText={setNewName}
              autoFocus
              onSubmitEditing={createProject}
            />
            <View style={s.modalButtons}>
              <TouchableOpacity style={s.btnCancel} onPress={() => setShowModal(false)}>
                <Text style={s.btnCancelText}>Annuler</Text>
              </TouchableOpacity>
              <TouchableOpacity style={s.btnCreate} onPress={createProject}>
                <Text style={s.btnCreateText}>Créer</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const s = StyleSheet.create({
  container:      { flex: 1, backgroundColor: '#111' },
  empty:          { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 40 },
  emptyIcon:      { fontSize: 56, marginBottom: 16 },
  emptyTitle:     { color: '#fff', fontSize: 22, fontWeight: 'bold', marginBottom: 8 },
  emptyText:      { color: '#888', fontSize: 15, textAlign: 'center', lineHeight: 22 },
  card:           { flexDirection: 'row', backgroundColor: '#1e1e1e', marginHorizontal: 16, marginBottom: 10, borderRadius: 12, padding: 16, alignItems: 'center' },
  cardLeft:       { flex: 1 },
  cardName:       { color: '#fff', fontSize: 17, fontWeight: '600', marginBottom: 2 },
  cardActivity:   { color: '#4af', fontSize: 13, marginBottom: 2 },
  cardStep:       { color: '#f90', fontSize: 12, marginBottom: 2 },
  cardDate:       { color: '#666', fontSize: 12 },
  deleteBtn:      { padding: 8 },
  deleteBtnText:  { color: '#666', fontSize: 18 },
  fab:            { position: 'absolute', bottom: 32, left: 24, right: 24, backgroundColor: '#4af', borderRadius: 14, padding: 16, alignItems: 'center' },
  fabText:        { color: '#fff', fontSize: 17, fontWeight: 'bold' },
  modalOverlay:   { flex: 1, backgroundColor: '#000a', justifyContent: 'flex-end' },
  modalBox:       { backgroundColor: '#1e1e1e', borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 24 },
  modalTitle:     { color: '#fff', fontSize: 20, fontWeight: 'bold', marginBottom: 16 },
  input:          { backgroundColor: '#2a2a2a', color: '#fff', borderRadius: 10, padding: 14, fontSize: 16, marginBottom: 16 },
  modalButtons:   { flexDirection: 'row', gap: 12 },
  btnCancel:      { flex: 1, backgroundColor: '#2a2a2a', borderRadius: 10, padding: 14, alignItems: 'center' },
  btnCancelText:  { color: '#aaa', fontSize: 16 },
  btnCreate:      { flex: 1, backgroundColor: '#4af', borderRadius: 10, padding: 14, alignItems: 'center' },
  btnCreateText:  { color: '#fff', fontSize: 16, fontWeight: 'bold' },
});
