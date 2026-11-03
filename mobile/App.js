import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Alert, SafeAreaView, ScrollView, StatusBar, StyleSheet, Text, TextInput, TouchableOpacity, View } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import * as Location from 'expo-location';
import { StatusBar as ExpoStatusBar } from 'expo-status-bar';
import * as SecureStore from 'expo-secure-store';

const API = process.env.EXPO_PUBLIC_API_URL || 'http://localhost:8000/api';
const cats = ['Road damage', 'Garbage & waste', 'Streetlight', 'Drainage', 'Water leakage', 'Sanitation'];

export default function App() {
  const [tab, setTab] = useState('Home');
  const [reports, setReports] = useState([]);
  const [user, setUser] = useState(null);
  const [accessToken, setAccessToken] = useState(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [authLoading, setAuthLoading] = useState(false);
  const [authError, setAuthError] = useState('');
  const [authMode, setAuthMode] = useState('login');
  const [authName, setAuthName] = useState('');
  const [authEmail, setAuthEmail] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authCode, setAuthCode] = useState('');
  const [category, setCategory] = useState(cats[0]);
  const [description, setDescription] = useState('');
  const [title, setTitle] = useState('');
  const [coords, setCoords] = useState(null);
  const [manualLatitude, setManualLatitude] = useState('');
  const [manualLongitude, setManualLongitude] = useState('');
  const [place, setPlace] = useState('Location not added yet');
  const [photo, setPhoto] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let active = true;
    SecureStore.getItemAsync('civicops_access_token')
      .then(async token => {
        if (!token) return;
        const response = await fetch(`${API}/auth/me`, { headers: { Authorization: `Bearer ${token}` } });
        if (!response.ok) throw new Error('Your session expired. Please sign in again.');
        const profile = await response.json();
        if (active && profile.role === 'Citizen') { setAccessToken(token); setUser(profile); }
      })
      .catch(async () => { await SecureStore.deleteItemAsync('civicops_access_token'); })
      .finally(() => { if (active) setSessionLoading(false); });
    return () => { active = false; };
  }, []);

  const authenticate = async () => {
    if (authMode === 'register' && !authName.trim()) { setAuthError('Enter your name.'); return; }
    if (!authEmail.trim() || !authPassword) { setAuthError('Enter your email and password.'); return; }
    setAuthLoading(true);
    setAuthError('');
    try {
      const payload = { email: authEmail.trim(), password: authPassword };
      if (authMode === 'register') {
        payload.name = authName.trim();
        if (authCode.trim()) payload.access_code = authCode.trim();
      }
      const response = await fetch(`${API}/auth/${authMode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Authentication failed.');
      }
      const result = await response.json();
      if (result.user.role !== 'Citizen') {
        throw new Error('Staff accounts should use the CiviCops web dashboard.');
      }
      await SecureStore.setItemAsync('civicops_access_token', result.access_token);
      setAccessToken(result.access_token);
      setUser(result.user);
    } catch (error) {
      setAuthError(error.message);
    } finally {
      setAuthLoading(false);
    }
  };

  useEffect(() => {
    if (!accessToken) return;
    fetch(`${API}/reports`, { headers: { Authorization: `Bearer ${accessToken}` } })
      .then(async response => {
        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.detail || 'Could not load reports.');
        }
        return response.json();
      })
      .then(setReports)
      .catch(error => Alert.alert('Reports unavailable', error.message));
  }, [accessToken]);

  const locate = async () => {
    const permission = await Location.requestForegroundPermissionsAsync();
    if (permission.status !== 'granted') { Alert.alert('Location permission', 'Allow location access to attach accurate GPS coordinates to the report.'); return; }
    const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
    const location = { latitude: pos.coords.latitude, longitude: pos.coords.longitude };
    setCoords(location);
    setManualLatitude(String(location.latitude));
    setManualLongitude(String(location.longitude));
    setPlace(`${location.latitude.toFixed(5)}, ${location.longitude.toFixed(5)}`);
  };
  const updateManualCoordinates = (latitude, longitude) => {
    setManualLatitude(latitude);
    setManualLongitude(longitude);
    const location = { latitude: Number(latitude), longitude: Number(longitude) };
    if (latitude !== '' && longitude !== '' && Number.isFinite(location.latitude) && location.latitude >= -90 && location.latitude <= 90 && Number.isFinite(location.longitude) && location.longitude >= -180 && location.longitude <= 180) {
      setCoords(location);
      setPlace(`${location.latitude.toFixed(5)}, ${location.longitude.toFixed(5)}`);
    } else {
      setCoords(null);
    }
  };
  useEffect(() => {
    if (tab === 'Report' && !coords) {
      locate().catch(error => Alert.alert('Location unavailable', error.message));
    }
  }, [tab, coords]);
  const addPhoto = async () => {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) { Alert.alert('Photo permission', 'Allow photo library access to attach evidence.'); return; }
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], quality: .75 });
    if (!result.canceled) {
      const selected = result.assets[0];
      if (selected.fileSize && selected.fileSize > 20 * 1024 * 1024) {
        Alert.alert('Image too large', 'Choose an image 20 MB or smaller.');
        return;
      }
      setPhoto(selected);
    }
  };
  const submit = async () => {
    if (description.trim().length < 8) { Alert.alert('Add a few details', 'Please describe the issue in at least 8 characters.'); return; }
    if (!coords) { Alert.alert('Add the location', 'Use your current location or add location access before submitting.'); return; }
    setLoading(true);
    let uploadedMedia = null;
    try {
      if (photo) {
        const formData = new FormData();
        formData.append('file', {
          uri: photo.uri,
          name: photo.fileName || `report-image.${photo.mimeType === 'image/png' ? 'png' : photo.mimeType === 'image/webp' ? 'webp' : 'jpg'}`,
          type: photo.mimeType || 'image/jpeg',
        });
        const uploadResponse = await fetch(`${API}/media`, {
          method: 'POST',
          headers: { Authorization: `Bearer ${accessToken}` },
          body: formData,
        });
        if (!uploadResponse.ok) {
          const error = await uploadResponse.json();
          throw new Error(error.detail || 'Image could not be uploaded.');
        }
        uploadedMedia = await uploadResponse.json();
      }
      const payload = { title: title.trim() || `${category} near ${place}`, description, category, location: { ...coords, address: place }, media: uploadedMedia ? [uploadedMedia] : [] };
      const response = await fetch(`${API}/reports`, { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${accessToken}` }, body: JSON.stringify(payload) });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Report could not be saved.');
      }
      const created = await response.json(); setReports([created, ...reports]); setDescription(''); setTitle(''); setPhoto(null); setCoords(null); setManualLatitude(''); setManualLongitude(''); setPlace('Location not added yet'); setTab('Reports'); Alert.alert('Report submitted', `Reference ${created.report_id}`);
    } catch (error) {
      let message = error.message;
      if (uploadedMedia) {
        try {
          const cleanup = await fetch(`${API}/media/${encodeURIComponent(uploadedMedia.media_id)}`, {
            method: 'DELETE',
            headers: { Authorization: `Bearer ${accessToken}` },
          });
          if (!cleanup.ok) {
            const cleanupError = await cleanup.json();
            message += ` Image cleanup failed: ${cleanupError.detail || cleanup.statusText}`;
          }
        } catch (cleanupError) {
          message += ` Image cleanup failed: ${cleanupError.message}`;
        }
      }
      Alert.alert('Report not saved', message);
    }
    finally { setLoading(false); }
  };

  const signOut = async () => {
    await SecureStore.deleteItemAsync('civicops_access_token');
    setUser(null); setAccessToken(null); setReports([]); setTab('Home');
  };
  if (sessionLoading) return <SafeAreaView style={styles.safe}><ActivityIndicator color="#39795e"/></SafeAreaView>;
  if (!user) return <SafeAreaView style={styles.safe}><ExpoStatusBar style="dark"/><StatusBar barStyle="dark-content"/><ScrollView contentContainerStyle={{ flexGrow: 1, justifyContent: 'center', padding: 24 }}><View style={styles.brandMark}><Text style={styles.brandGlyph}>✓</Text></View><Text style={styles.heading}>Welcome to CiviCops</Text><Text style={styles.subheading}>{authMode === 'login' ? 'Sign in to report issues and track city updates.' : 'Create your CiviCops account.'}</Text>{authMode === 'register' ? <TextInput style={styles.input} placeholder="Full name" autoCapitalize="words" value={authName} onChangeText={setAuthName}/> : null}<TextInput style={styles.input} placeholder="Email address" autoCapitalize="none" keyboardType="email-address" value={authEmail} onChangeText={setAuthEmail}/><TextInput style={styles.input} placeholder="Password (at least 10 characters)" secureTextEntry value={authPassword} onChangeText={setAuthPassword}/>{authMode === 'register' ? <TextInput style={styles.input} placeholder="Invitation / first-admin code (if required)" autoCapitalize="none" value={authCode} onChangeText={setAuthCode}/> : null}{authLoading?<ActivityIndicator color="#39795e"/>:<TouchableOpacity style={styles.mainButton} onPress={authenticate}><Text style={styles.mainButtonText}>{authMode === 'login' ? 'Sign in' : 'Create account'}</Text></TouchableOpacity>}{authError? <Text style={{ color: '#a94442', fontSize: 12, textAlign: 'center' }}>{authError}</Text>:null}<TouchableOpacity onPress={()=>{setAuthMode(authMode==='login'?'register':'login');setAuthError('')}}><Text style={styles.link}>{authMode==='login'?'New here? Create an account':'Already registered? Sign in'}</Text></TouchableOpacity><Text style={styles.privacy}>Roles are assigned by email domain. Staff registration requires an invitation.</Text></ScrollView></SafeAreaView>;

  return <SafeAreaView style={styles.safe}><ExpoStatusBar style="dark"/><StatusBar barStyle="dark-content"/><View style={styles.header}><View style={styles.brandMark}><Text style={styles.brandGlyph}>✓</Text></View><View style={{ flex: 1 }}><Text style={styles.brand}>CiviCops</Text><Text style={styles.tagline}>{user.name}</Text></View><TouchableOpacity style={styles.bell} onPress={signOut}><Text>Sign out</Text></TouchableOpacity></View>
    {tab === 'Home' ? <ScrollView contentContainerStyle={styles.content}><Text style={styles.eyebrow}>TUESDAY, OCTOBER 6</Text><Text style={styles.heading}>Good morning, Alex</Text><Text style={styles.subheading}>Your voice can make a difference in Northbridge.</Text><View style={styles.hero}><Text style={styles.heroKicker}>YOUR NEIGHBORHOOD, YOUR VOICE</Text><Text style={styles.heroTitle}>Small actions.{ '\n' }Big city change.</Text><Text style={styles.heroText}>See something that needs fixing? Let’s get it to the right people.</Text><TouchableOpacity style={styles.heroButton} onPress={() => setTab('Report')}><Text style={styles.heroButtonText}>＋  Report an issue     →</Text></TouchableOpacity></View><View style={styles.stats}><View style={styles.stat}><Text style={styles.statValue}>12</Text><Text style={styles.statLabel}>Reports submitted</Text></View><View style={styles.stat}><Text style={styles.statValue}>9</Text><Text style={styles.statLabel}>Issues resolved</Text></View></View><View style={styles.sectionRow}><Text style={styles.section}>Recent updates</Text><TouchableOpacity onPress={() => setTab('Reports')}><Text style={styles.link}>See all →</Text></TouchableOpacity></View>{reports.slice(0,3).map(r => <ReportCard key={r.report_id || r.id} report={r}/>)}</ScrollView> : tab === 'Reports' ? <ScrollView contentContainerStyle={styles.content}><Text style={styles.eyebrow}>YOUR NEIGHBORHOOD</Text><Text style={styles.heading}>My reports</Text><Text style={styles.subheading}>Track updates from your city teams.</Text><TouchableOpacity style={styles.mainButton} onPress={() => setTab('Report')}><Text style={styles.mainButtonText}>＋  Report an issue</Text></TouchableOpacity>{reports.map(r => <ReportCard key={r.report_id || r.id} report={r}/>)}</ScrollView> : <ScrollView contentContainerStyle={styles.content}><TouchableOpacity onPress={() => setTab('Home')}><Text style={styles.link}>← Back</Text></TouchableOpacity><Text style={[styles.heading, { marginTop: 16 }]}>Report an issue</Text><Text style={styles.subheading}>Help the right city team understand what’s happening.</Text><Text style={styles.label}>ISSUE CATEGORY</Text><View style={styles.chips}>{cats.map(c => <TouchableOpacity key={c} onPress={() => setCategory(c)} style={[styles.chip, category === c && styles.chipActive]}><Text style={[styles.chipText, category === c && styles.chipTextActive]}>{c}</Text></TouchableOpacity>)}</View><Text style={styles.label}>SHORT TITLE · OPTIONAL</Text><TextInput style={styles.input} placeholder="e.g. Pothole near the bus stop" value={title} onChangeText={setTitle}/><Text style={styles.label}>DESCRIBE THE ISSUE</Text><TextInput style={[styles.input, styles.textArea]} placeholder="What happened? Is anyone at risk?" multiline value={description} onChangeText={setDescription}/><TouchableOpacity style={styles.optionButton} onPress={addPhoto}><Text style={styles.optionIcon}>▧</Text><View style={{ flex: 1 }}><Text style={styles.optionTitle}>{photo ? 'Evidence added' : 'Add a photo or video'}</Text><Text style={styles.optionHint}>{photo ? photo.fileName || 'Tap to replace' : 'A clear photo helps the city team'}</Text></View><Text style={styles.link}>＋</Text></TouchableOpacity><TouchableOpacity style={styles.optionButton} onPress={() => locate().catch(error => Alert.alert('Location unavailable', error.message))}><Text style={styles.optionIcon}>⌖</Text><View style={{ flex: 1 }}><Text style={styles.optionTitle}>Issue location</Text><Text style={styles.optionHint}>{place}</Text></View><Text style={styles.link}>{coords ? '✓' : 'Add'}</Text></TouchableOpacity><Text style={styles.optionHint}>If GPS permission is unavailable, enter the location coordinates:</Text><TextInput style={styles.input} placeholder="Latitude (e.g. 40.7128)" keyboardType="decimal-pad" value={manualLatitude} onChangeText={value => updateManualCoordinates(value, manualLongitude)}/><TextInput style={styles.input} placeholder="Longitude (e.g. -74.0060)" keyboardType="decimal-pad" value={manualLongitude} onChangeText={value => updateManualCoordinates(manualLatitude, value)}/><TouchableOpacity style={styles.mainButton} disabled={loading} onPress={submit}>{loading ? <ActivityIndicator color="#fff"/> : <Text style={styles.mainButtonText}>Submit report   →</Text>}</TouchableOpacity><Text style={styles.privacy}>Your contact details are only visible to city staff.</Text></ScrollView>}
    <View style={styles.tabbar}>{[['Home','⌂'],['Reports','▤'],['Report','＋']].map(([name,icon]) => <TouchableOpacity key={name} onPress={() => setTab(name)} style={styles.tab}><Text style={[styles.tabIcon, tab === name && styles.tabOn]}>{icon}</Text><Text style={[styles.tabLabel, tab === name && styles.tabOn]}>{name}</Text></TouchableOpacity>)}</View></SafeAreaView>;
}

function ReportCard({ report }) {
  const status = report.status || 'Submitted';
  return <View style={styles.reportCard}><View style={styles.cardIcon}><Text>⌁</Text></View><View style={{ flex: 1 }}><Text style={styles.cardTitle}>{report.title}</Text><Text style={styles.cardMeta}>{report.location?.address || report.area || report.category} · {report.report_id || report.id}</Text><View style={styles.cardBottom}><Text style={[styles.badge, status === 'Resolved' ? styles.resolved : status === 'In progress' ? styles.progress : styles.pending]}>{status}</Text><Text style={styles.arrow}>›</Text></View></View></View>;
}

const styles = StyleSheet.create({ safe: { flex: 1, backgroundColor: '#f5f7f7' }, header: { height: 62, backgroundColor: '#fff', flexDirection: 'row', alignItems: 'center', paddingHorizontal: 18, borderBottomWidth: 1, borderBottomColor: '#e9edec' }, brandMark: { width: 34, height: 34, borderRadius: 11, backgroundColor: '#39795e', alignItems: 'center', justifyContent: 'center', marginRight: 10 }, brandGlyph: { color: '#fff', fontWeight: '800', fontSize: 18 }, brand: { color: '#293536', fontSize: 16, fontWeight: '800' }, tagline: { color: '#88938f', fontSize: 10 }, bell: { width: 34, height: 34, borderRadius: 17, backgroundColor: '#f4f6f5', alignItems: 'center', justifyContent: 'center' }, content: { padding: 19, paddingBottom: 30 }, eyebrow: { color: '#7c9184', fontSize: 9, letterSpacing: 1.1, fontWeight: '700', marginTop: 9 }, heading: { fontSize: 24, fontWeight: '800', color: '#2c3937', marginTop: 7 }, subheading: { color: '#84908c', fontSize: 12, lineHeight: 18, marginTop: 5, marginBottom: 18 }, hero: { padding: 19, borderRadius: 13, backgroundColor: '#e5efe5', marginBottom: 16 }, heroKicker: { color: '#6a8572', fontSize: 8, letterSpacing: 1, fontWeight: '700' }, heroTitle: { color: '#2e4a3b', fontSize: 25, lineHeight: 31, fontWeight: '800', marginTop: 13 }, heroText: { color: '#6e8076', fontSize: 10, lineHeight: 16, marginTop: 5, maxWidth: 230 }, heroButton: { alignSelf: 'flex-start', backgroundColor: '#39795e', padding: 11, borderRadius: 7, marginTop: 14 }, heroButtonText: { color: '#fff', fontWeight: '700', fontSize: 11 }, stats: { flexDirection: 'row', gap: 10, marginBottom: 20 }, stat: { flex: 1, backgroundColor: '#fff', borderColor: '#e9edec', borderWidth: 1, padding: 13, borderRadius: 9 }, statValue: { fontSize: 21, fontWeight: '800', color: '#33413c' }, statLabel: { fontSize: 9, color: '#89938f', marginTop: 3 }, sectionRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }, section: { fontSize: 14, color: '#33413c', fontWeight: '700' }, link: { color: '#39795e', fontSize: 10, fontWeight: '700' }, reportCard: { backgroundColor: '#fff', padding: 12, borderWidth: 1, borderColor: '#e9edec', borderRadius: 9, marginBottom: 8, flexDirection: 'row', gap: 10, alignItems: 'center' }, cardIcon: { width: 31, height: 31, borderRadius: 8, backgroundColor: '#f0f4ef', alignItems: 'center', justifyContent: 'center' }, cardTitle: { color: '#36433f', fontSize: 11, fontWeight: '700' }, cardMeta: { color: '#939d99', fontSize: 8, marginTop: 4 }, cardBottom: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }, badge: { overflow: 'hidden', borderRadius: 12, paddingHorizontal: 8, paddingVertical: 4, fontSize: 8, fontWeight: '600' }, pending: { color: '#7d8885', backgroundColor: '#f1f3f2' }, progress: { color: '#5275b1', backgroundColor: '#edf2fc' }, resolved: { color: '#4b8a64', backgroundColor: '#eaf4ed' }, arrow: { color: '#9ba5a1', fontSize: 18 }, tabbar: { height: 60, backgroundColor: '#fff', borderTopWidth: 1, borderColor: '#e8edeb', flexDirection: 'row', justifyContent: 'space-around', alignItems: 'center' }, tab: { minWidth: 64, alignItems: 'center', gap: 3 }, tabIcon: { color: '#929d98', fontSize: 18, height: 21 }, tabLabel: { color: '#8a9590', fontSize: 8 }, tabOn: { color: '#39795e', fontWeight: '800' }, mainButton: { backgroundColor: '#39795e', borderRadius: 8, padding: 13, alignItems: 'center', marginVertical: 13 }, mainButtonText: { color: '#fff', fontWeight: '700', fontSize: 11 }, label: { color: '#697771', fontSize: 9, letterSpacing: .5, fontWeight: '700', marginTop: 13, marginBottom: 7 }, chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 7 }, chip: { borderColor: '#e1e8e3', borderWidth: 1, borderRadius: 16, paddingHorizontal: 10, paddingVertical: 7, backgroundColor: '#fff' }, chipActive: { backgroundColor: '#edf5ee', borderColor: '#89aa91' }, chipText: { color: '#707d76', fontSize: 9 }, chipTextActive: { color: '#39795e', fontWeight: '700' }, input: { backgroundColor: '#fff', borderColor: '#e4eae6', borderWidth: 1, borderRadius: 8, padding: 11, fontSize: 11, color: '#384640' }, textArea: { minHeight: 95, textAlignVertical: 'top' }, optionButton: { backgroundColor: '#fff', borderColor: '#e8edeb', borderWidth: 1, borderRadius: 8, padding: 11, marginTop: 9, flexDirection: 'row', alignItems: 'center', gap: 10 }, optionIcon: { color: '#568065', fontSize: 20 }, optionTitle: { color: '#485650', fontSize: 10, fontWeight: '700' }, optionHint: { color: '#929d98', fontSize: 8, marginTop: 3 }, privacy: { color: '#929d98', textAlign: 'center', fontSize: 8, marginBottom: 10 } });
