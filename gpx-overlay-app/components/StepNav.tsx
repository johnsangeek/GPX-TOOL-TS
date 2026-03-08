import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { useRouter } from 'expo-router';

const STEPS = [
  { name: 'step1-source',  label: '1·GPX' },
  { name: 'step2-videos',  label: '2·Vidéos' },
  { name: 'step3-sync',    label: '3·Sync' },
  { name: 'step4-widgets', label: '4·Widgets' },
  { name: 'step4-layout',  label: '4·Layout' },
  { name: 'step5-export',  label: '5·Export' },
];

export default function StepNav({ current, projectId }: { current: string; projectId: string }) {
  const router = useRouter();
  const idx = STEPS.findIndex(s => s.name === current);
  const prev = idx > 0 ? STEPS[idx - 1] : null;
  const next = idx < STEPS.length - 1 ? STEPS[idx + 1] : null;

  function go(step: string) {
    router.push({ pathname: `/(project)/${step}` as any, params: { projectId } });
  }

  return (
    <View style={s.bar}>
      {prev
        ? <TouchableOpacity style={s.btn} onPress={() => go(prev.name)}><Text style={s.arrow}>← {prev.label}</Text></TouchableOpacity>
        : <View style={s.spacer} />}
      <Text style={s.dot}>●</Text>
      {next
        ? <TouchableOpacity style={s.btn} onPress={() => go(next.name)}><Text style={s.arrow}>{next.label} →</Text></TouchableOpacity>
        : <View style={s.spacer} />}
    </View>
  );
}

const s = StyleSheet.create({
  bar:    { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#222' },
  btn:    { backgroundColor: '#1e1e1e', paddingHorizontal: 12, paddingVertical: 7, borderRadius: 8, borderWidth: 1, borderColor: '#333' },
  arrow:  { color: '#4af', fontSize: 13, fontWeight: '600' },
  dot:    { color: '#333', fontSize: 10 },
  spacer: { width: 90 },
});
