/**
 * Store projets — persisté dans AsyncStorage
 * Format : liste de Project sauvegardée en JSON
 */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { Project } from '../types';

const KEY = 'gpx_overlay_projects';

export async function loadProjects(): Promise<Project[]> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export async function saveProject(project: Project): Promise<void> {
  const projects = await loadProjects();
  const idx = projects.findIndex(p => p.id === project.id);
  if (idx >= 0) {
    projects[idx] = project;
  } else {
    projects.unshift(project);
  }
  await AsyncStorage.setItem(KEY, JSON.stringify(projects));
}

export async function deleteProject(id: string): Promise<void> {
  const projects = await loadProjects();
  await AsyncStorage.setItem(KEY, JSON.stringify(projects.filter(p => p.id !== id)));
}

export function newProject(name: string): Project {
  return {
    id: Math.random().toString(36).slice(2),
    name,
    createdAt: new Date().toISOString(),
  };
}
