import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';

export default function RootLayout() {
  return (
    <>
      <StatusBar style="light" />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: '#111' },
          headerTintColor: '#fff',
          headerTitleStyle: { fontWeight: 'bold' },
          contentStyle: { backgroundColor: '#111' },
        }}
      >
        <Stack.Screen name="index" options={{ title: 'GPX Overlay', headerShown: true }} />
        <Stack.Screen name="(project)" options={{ headerShown: false }} />
      </Stack>
    </>
  );
}
