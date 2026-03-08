import { Stack } from 'expo-router';

export default function ProjectLayout() {
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: '#111' },
        headerTintColor: '#fff',
        headerTitleStyle: { fontWeight: 'bold' },
        contentStyle: { backgroundColor: '#111' },
      }}
    >
      <Stack.Screen name="step1-source"  options={{ title: 'Source GPX' }} />
      <Stack.Screen name="step2-videos"  options={{ title: 'Vidéos' }} />
      <Stack.Screen name="step3-sync"    options={{ title: 'Synchronisation' }} />
      <Stack.Screen name="step4-widgets" options={{ title: 'Widgets' }} />
      <Stack.Screen name="step4-layout"  options={{ title: 'Layout' }} />
      <Stack.Screen name="step5-export"  options={{ title: 'Export' }} />
    </Stack>
  );
}
