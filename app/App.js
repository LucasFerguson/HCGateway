import { StyleSheet, Text, View, TextInput, Button, Switch, Modal, PermissionsAndroid, Platform, ScrollView } from 'react-native';
import React from 'react';
import { StatusBar } from 'expo-status-bar';
import {
  initialize,
  requestPermission,
  readRecords,
  insertRecords,
  deleteRecordsByUuids,
  getGrantedPermissions,
} from 'react-native-health-connect';
import AsyncStorage from '@react-native-async-storage/async-storage';
import Toast from 'react-native-toast-message';
import axios from 'axios';
import ReactNativeForegroundService from '@supersami/rn-foreground-service';
import { requestNotifications } from 'react-native-permissions';
import * as Sentry from '@sentry/react-native';
import messaging from '@react-native-firebase/messaging';
import { Notifications } from 'react-native-notifications';
import DateTimePicker, { useDefaultStyles } from 'react-native-ui-datepicker';

const setObj = async (key, value) => { try { const jsonValue = JSON.stringify(value); await AsyncStorage.setItem(key, jsonValue) } catch (e) { console.log(e) } }
const setPlain = async (key, value) => { try { await AsyncStorage.setItem(key, value) } catch (e) { console.log(e) } }
const get = async (key) => { try { const value = await AsyncStorage.getItem(key); if (value !== null) { try { return JSON.parse(value) } catch { return value } } } catch (e) { console.log(e) } }
const delkey = async (key, value) => { try { await AsyncStorage.removeItem(key) } catch (e) { console.log(e) } }
const getAll = async () => { try { const keys = await AsyncStorage.getAllKeys(); return keys } catch (error) { console.error(error) } }

Notifications.setNotificationChannel({
  channelId: 'push-errors',
  name: 'Push Errors',
  importance: 5,
  description: 'Alerts for push errors',
  groupId: 'push-errors',
  groupName: 'Errors',
  enableLights: true,
  enableVibration: true,
  showBadge: true,
  vibrationPattern: [200, 1000, 500, 1000, 500],
})


//  Probably don't need centralized Sentry error handling for now
//  2025-04-01 - Lucas

let isSentryEnabled = false;
// get('sentryEnabled')
//   .then(res => {
//     if (res != "false") {
//       Sentry.init({
//         dsn: 'https://e4a201b96ea602d28e90b5e4bbe67aa6@sentry.shuchir.dev/6',
//         // enableSpotlight: __DEV__,
//       });
//       Toast.show({
//         type: 'success',
//         text1: "Sentry enabled from settings",
//       });
//     } else {
//       isSentryEnabled = false;
//       Toast.show({
//         type: 'info',
//         text1: "Sentry is disabled",
//       });
//     }
//   })
//   .catch(err => {
//     console.log(err);
//     Toast.show({
//       type: 'error',
//       text1: "Failed to check Sentry settings",
//     });
//   });


ReactNativeForegroundService.register();

const requestUserPermission = async () => {
  try {
    await messaging().requestPermission();
    const token = await messaging().getToken();
    console.log('Device Token:', token);
    return token;
  } catch (error) {
    console.log('Permission or Token retrieval error:', error);
  }
};

messaging().setBackgroundMessageHandler(async remoteMessage => {
  if (remoteMessage.data.op == "PUSH") handlePush(remoteMessage.data);
  if (remoteMessage.data.op == "DEL") handleDel(remoteMessage.data);
});

messaging().onMessage(remoteMessage => {
  if (remoteMessage.data.op == "PUSH") handlePush(remoteMessage.data);
  if (remoteMessage.data.op == "DEL") handleDel(remoteMessage.data);
});

let login;
// let apiBase = 'https://api.hcgateway.shuchir.dev'; // need to change this - Lucas 2025-04-01
let apiBase = 'http://192.168.8.239:6644/'; // need to change this - Lucas 2025-04-01
let lastSync = null;
let lastSuccessfulSyncAt = null;
let lastSyncAttemptAt = null;
let lastSyncError = null;
let taskDelay = 7200 * 1000; // 2 hours
let fullSyncMode = true; // Default to full history sync (see historyDays)
let historyDays = 30; // How many days back a full sync reaches. Health Connect
let forceResyncSyncedDays = false;
let syncedDaysByType = {};
let syncInventory = null;
// caps reads at 30 days unless the READ_HEALTH_DATA_HISTORY permission is
// granted, after which older data can be read. See requestHistoryPermission().
let syncInFlight = null;
let foregroundTasksRegistered = false;

const RECORD_TYPES = ["ActiveCaloriesBurned", "BasalBodyTemperature", "BloodGlucose", "BloodPressure", "BasalMetabolicRate", "BodyFat", "BodyTemperature", "BoneMass", "CyclingPedalingCadence", "CervicalMucus", "ExerciseSession", "Distance", "ElevationGained", "FloorsClimbed", "HeartRate", "Height", "Hydration", "LeanBodyMass", "MenstruationFlow", "MenstruationPeriod", "Nutrition", "OvulationTest", "OxygenSaturation", "Power", "RespiratoryRate", "RestingHeartRate", "SleepSession", "Speed", "Steps", "StepsCadence", "TotalCaloriesBurned", "Vo2Max", "Weight", "WheelchairPushes"];
const READ_PERMISSIONS = RECORD_TYPES.map(recordType => ({ accessType: 'read', recordType }));
const PAGE_SIZE = 1000;
const UPLOAD_BATCH_SIZE = 250;
const INCREMENTAL_OVERLAP_MS = 10 * 60 * 1000;
const SYNCED_DAYS_KEY = 'syncedDaysByType';
const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

const HEALTH_HISTORY_PERMISSION = 'android.permission.health.READ_HEALTH_DATA_HISTORY';
const HEALTH_BACKGROUND_PERMISSION = 'android.permission.health.READ_HEALTH_DATA_IN_BACKGROUND';
const HEALTH_HISTORY_RECORD_TYPE = 'ReadHealthDataHistory';
const HEALTH_BACKGROUND_RECORD_TYPE = 'BackgroundAccessPermission';

const requestRawAndroidPermission = async (permission, label) => {
  try {
    if (Platform.OS !== 'android') return false;
    const already = await PermissionsAndroid.check(permission);
    if (already) return true;
    const result = await PermissionsAndroid.request(permission);
    return result === PermissionsAndroid.RESULTS.GRANTED;
  } catch (err) {
    console.log(`${label} permission request failed`, err);
    return false;
  }
};

const requestSpecialHealthPermission = async (recordType, androidPermission, label) => {
  try {
    if (Platform.OS !== 'android') return false;
    if (await PermissionsAndroid.check(androidPermission)) return true;
    const granted = await requestPermission([{ accessType: 'read', recordType }]);
    if ((granted || []).some(permission => permission.accessType === 'read' && permission.recordType === recordType)) {
      return true;
    }
    return await PermissionsAndroid.check(androidPermission);
  } catch (err) {
    console.log(`${label} Health Connect permission request failed`, err);
    return requestRawAndroidPermission(androidPermission, label);
  }
};

// Request the READ_HEALTH_DATA_HISTORY runtime permission. Returns true if
// granted (or already granted). No-op-returns-false on non-Android platforms.
const requestHistoryPermission = async () => {
  return requestSpecialHealthPermission(HEALTH_HISTORY_RECORD_TYPE, HEALTH_HISTORY_PERMISSION, 'history');
};

const requestBackgroundPermission = async () => {
  return requestSpecialHealthPermission(HEALTH_BACKGROUND_RECORD_TYPE, HEALTH_BACKGROUND_PERMISSION, 'background read');
};

// ---------------------------------------------------------------------------
// Sync status store
// sync() runs at module scope (also from the background foreground-service),
// so it can't call React setState directly. Instead it mutates this object and
// calls notifySyncStatus(); the App component registers a listener to re-render.
// Per-type states: 'pending' | 'syncing' | 'done' | 'failed' | 'skipped'
// ---------------------------------------------------------------------------
let syncStatus = {
  running: false,
  startedAt: null,
  finishedAt: null,
  phase: 'idle',
  error: null,
  failedTypes: [],
  pagesRead: 0,
  uploadRequests: 0,
  invalidRecords: 0,
  skippedSyncedRecords: 0,
  syncedDaysUpdated: 0,
  windowStart: null,
  windowEnd: null,
  forceResyncSyncedDays,
  historyPermissionGranted: null,
  backgroundPermissionGranted: null,
  lastSuccessfulSyncAt,
  lastAttemptAt: lastSyncAttemptAt,
  numRecords: 0,
  numRecordsSynced: 0,
  currentType: null,
  types: {}, // { [recordType]: { state, count, synced, error } }
};
let syncStatusListener = null;
const STATUS_BADGE = {
  pending: { label: 'pending', color: '#9e9e9e' },
  syncing: { label: 'syncing', color: '#1e88e5' },
  done: { label: 'done', color: '#2e7d32' },
  failed: { label: 'failed', color: '#c62828' },
  permission_missing: { label: 'no access', color: '#ef6c00' },
  skipped: { label: 'none', color: '#bdbdbd' },
  locally_synced: { label: 'synced', color: '#607d8b' },
};
const notifySyncStatus = () => { try { if (syncStatusListener) syncStatusListener(); } catch (e) { console.log(e) } };
const setTypeStatus = (type, patch) => {
  syncStatus.types[type] = { ...(syncStatus.types[type] || { state: 'pending', count: 0, synced: 0, error: null }), ...patch };
  notifySyncStatus();
};

Toast.show({
  type: 'info',
  text1: "Loading API Base URL...",
  autoHide: false
})
get('apiBase')
  .then(res => {
    if (res) {
      apiBase = res;
      Toast.hide();
      Toast.show({
        type: "success",
        text1: "API Base URL loaded",
      })
    }
    else {
      Toast.hide();
      Toast.show({
        type: "error",
        text1: "API Base URL not found. Using default server.",
      })
    }
  })

get('login')
  .then(res => {
    if (res) {
      login = res;
    }
  })

get('lastSync')
  .then(res => {
    if (res) {
      lastSync = res;
    }
  })

get('lastSuccessfulSyncAt')
  .then(res => {
    if (res) {
      lastSuccessfulSyncAt = res;
    }
  })

get('lastSyncAttemptAt')
  .then(res => {
    if (res) {
      lastSyncAttemptAt = res;
    }
  })

get('lastSyncError')
  .then(res => {
    if (res) {
      lastSyncError = res;
    }
  })

get('fullSyncMode')
  .then(res => {
    if (res !== null) {
      fullSyncMode = res === 'true';
    }
  })

get('historyDays')
  .then(res => {
    if (res !== null && !isNaN(Number(res))) {
      historyDays = Number(res);
    }
  })

get('forceResyncSyncedDays')
  .then(res => {
    if (res !== null) {
      forceResyncSyncedDays = res === 'true';
    }
  })

get(SYNCED_DAYS_KEY)
  .then(res => {
    if (res && typeof res === 'object') {
      syncedDaysByType = res;
    }
  })

get('syncInventory')
  .then(res => {
    if (res && typeof res === 'object') {
      syncInventory = res;
    }
  })

const askForPermissions = async () => {
  const isInitialized = await initialize();

  if (!isInitialized) {
    Toast.show({
      type: 'error',
      text1: "Health Connect unavailable",
      text2: "Install or enable Health Connect, then retry."
    });
    return;
  }

  const grantedPermissions = await requestPermission(READ_PERMISSIONS);
  await requestBackgroundPermission();

  console.log(grantedPermissions);

  if (grantedPermissions.length < READ_PERMISSIONS.length) {
    Toast.show({
      type: 'error',
      text1: "Permissions not granted",
      text2: "Please visit Health Connect settings to grant read permissions."
    })
  }
};

const refreshTokenFunc = async () => {
  let refreshToken = await get('refreshToken');
  if (!refreshToken) return;
  try {
    let response = await axios.post(`${apiBase}/api/v2/refresh`, {
      refresh: refreshToken
    });
    if ('token' in response.data) {
      console.log(response.data);
      await setPlain('login', response.data.token)
      login = response.data.token;
      await setPlain('refreshToken', response.data.refresh);
      Toast.show({
        type: 'success',
        text1: "Token refreshed successfully",
      })
    }
    else {
      Toast.show({
        type: 'error',
        text1: "Token refresh failed",
        text2: response.data.error
      })
      login = null;
      delkey('login');
    }
  }

  catch (err) {
    Toast.show({
      type: 'error',
      text1: "Token refresh failed",
      text2: err.message
    })
    login = null;
    delkey('login');
  }
}

const chunkArray = (items, size) => {
  const chunks = [];
  for (let i = 0; i < items.length; i += size) {
    chunks.push(items.slice(i, i + size));
  }
  return chunks;
};

const formatLocalDateKey = (date) => {
  const d = new Date(date);
  if (Number.isNaN(d.getTime())) return null;
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

const recordStartTime = (record) => record.time || record.startTime || null;
const recordEndTime = (record) => record.endTime || record.time || record.startTime || null;

const dateKeysForRecord = (record) => {
  const start = recordStartTime(record);
  const end = recordEndTime(record);
  const first = formatLocalDateKey(start);
  const last = formatLocalDateKey(end);
  if (!first) return [];
  if (!last || first === last) return [first];

  const keys = [];
  const cursor = new Date(start);
  cursor.setHours(0, 0, 0, 0);
  const lastDay = new Date(end);
  lastDay.setHours(0, 0, 0, 0);
  while (cursor.getTime() <= lastDay.getTime()) {
    keys.push(formatLocalDateKey(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return keys.filter(Boolean);
};

const isRecordIntervalValid = (record) => {
  const start = recordStartTime(record);
  const end = recordEndTime(record);
  if (!start) return { valid: false, reason: 'missing start/time' };
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : startMs;
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) {
    return { valid: false, reason: 'invalid start/end timestamp' };
  }
  if (endMs < startMs) {
    return { valid: false, reason: `end before start (${start} > ${end})` };
  }
  return { valid: true };
};

const summarizeDates = (days) => {
  const sorted = [...new Set(days)].sort();
  if (sorted.length === 0) return 'none';
  if (sorted.length === 1) return sorted[0];
  return `${sorted[0]} to ${sorted[sorted.length - 1]} (${sorted.length} days)`;
};

const countTrackedDays = (dayMapByType) => {
  const allDays = new Set();
  Object.values(dayMapByType || {}).forEach(dayMap => {
    Object.keys(dayMap || {}).forEach(day => allDays.add(day));
  });
  return allDays.size;
};

const trackedDaysSummary = (dayMapByType) => {
  const allDays = new Set();
  Object.values(dayMapByType || {}).forEach(dayMap => {
    Object.keys(dayMap || {}).forEach(day => allDays.add(day));
  });
  return summarizeDates([...allDays]);
};

const splitRecordsForUpload = (recordType, records) => {
  const dayMap = syncedDaysByType[recordType] || {};
  const valid = [];
  const invalid = [];
  const locallySynced = [];

  for (const record of records) {
    const interval = isRecordIntervalValid(record);
    if (!interval.valid) {
      invalid.push({ record, reason: interval.reason });
      continue;
    }

    const days = dateKeysForRecord(record);
    const alreadySynced = !forceResyncSyncedDays && days.length > 0 && days.every(day => dayMap[day]);
    if (alreadySynced) {
      locallySynced.push(record);
    } else {
      valid.push(record);
    }
  }

  return { valid, invalid, locallySynced };
};

const persistSyncedDays = async (recordType, records) => {
  if (records.length === 0) return 0;
  const dayMap = { ...(syncedDaysByType[recordType] || {}) };
  let changed = 0;
  const now = new Date().toISOString();
  for (const record of records) {
    for (const day of dateKeysForRecord(record)) {
      if (!dayMap[day]) changed += 1;
      dayMap[day] = now;
    }
  }
  if (changed > 0) {
    syncedDaysByType = { ...syncedDaysByType, [recordType]: dayMap };
    await setObj(SYNCED_DAYS_KEY, syncedDaysByType);
  }
  return changed;
};

const formatSyncError = (err) => {
  if (!err) return 'unknown error';
  if (err.response) return `${err.response.status}: ${JSON.stringify(err.response.data)}`;
  return String(err.message || err);
};

const updateForegroundProgress = (numRecordsSynced, numRecords) => {
  try {
    ReactNativeForegroundService.update({
      id: 1244,
      title: 'HCGateway Sync Progress',
      message: `HCGateway is currently syncing... [${numRecordsSynced}/${numRecords}]`,
      icon: 'ic_launcher',
      setOnlyAlertOnce: true,
      color: '#000000',
      progress: {
        max: Math.max(1, numRecords),
        curr: numRecordsSynced,
      }
    })
  }
  catch { }
};

const resetForegroundMessage = () => {
  try {
    ReactNativeForegroundService.update({
      id: 1244,
      title: 'HCGateway Sync Progress',
      message: `HCGateway is working in the background to sync your data.`,
      icon: 'ic_launcher',
      setOnlyAlertOnce: true,
      color: '#000000',
    })
  }
  catch { }
};

const refreshSyncInventory = async () => {
  if (!login) return null;
  try {
    const response = await axios.get(`${apiBase}/api/v2/analytics/inventory`, {
      headers: {
        "Authorization": `Bearer ${login}`
      }
    });
    syncInventory = { ...response.data, fetchedAt: new Date().toISOString() };
    await setObj('syncInventory', syncInventory);
    return syncInventory;
  } catch (err) {
    console.log('failed to refresh sync inventory', err);
    throw err;
  }
};

const getReadPermissionSet = async () => {
  try {
    const granted = await getGrantedPermissions();
    return new Set((granted || [])
      .filter(permission => permission.accessType === 'read')
      .map(permission => permission.recordType));
  } catch (err) {
    console.log('failed to read granted permissions', err);
    return null;
  }
};

const readAllRecords = async (recordType, timeRangeFilter) => {
  let pageToken;
  const records = [];
  do {
    const response = await readRecords(recordType, {
      timeRangeFilter,
      pageSize: PAGE_SIZE,
      ...(pageToken ? { pageToken } : {})
    });
    const pageRecords = response.records || [];
    records.push(...pageRecords);
    syncStatus.pagesRead += 1;
    syncStatus.numRecords += pageRecords.length;
    setTypeStatus(recordType, { count: records.length });
    notifySyncStatus();
    pageToken = response.pageToken;
  } while (pageToken);
  return records;
};

const postSyncBatch = async (recordType, batch) => {
  let refreshedForThisBatch = false;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      await axios.post(`${apiBase}/api/v2/sync/${recordType}`, {
        data: batch
      }, {
        headers: {
          "Authorization": `Bearer ${login}`
        }
      });
      syncStatus.uploadRequests += 1;
      notifySyncStatus();
      return;
    } catch (err) {
      if (err.response && err.response.status === 401 && !refreshedForThisBatch) {
        refreshedForThisBatch = true;
        await refreshTokenFunc();
        continue;
      }
      if (attempt === 3) throw err;
      await sleep((500 * Math.pow(2, attempt - 1)) + Math.floor(Math.random() * 250));
    }
  }
};

const uploadBatchIsolatingBadRecords = async (recordType, batch) => {
  try {
    await postSyncBatch(recordType, batch);
    return { uploaded: batch, rejected: [] };
  } catch (err) {
    if (batch.length === 1) {
      return { uploaded: [], rejected: [{ record: batch[0], reason: formatSyncError(err) }] };
    }
    const midpoint = Math.ceil(batch.length / 2);
    const left = await uploadBatchIsolatingBadRecords(recordType, batch.slice(0, midpoint));
    const right = await uploadBatchIsolatingBadRecords(recordType, batch.slice(midpoint));
    return {
      uploaded: [...left.uploaded, ...right.uploaded],
      rejected: [...left.rejected, ...right.rejected],
    };
  }
};

const uploadRecords = async (recordType, records) => {
  const { valid, invalid, locallySynced } = splitRecordsForUpload(recordType, records);
  syncStatus.invalidRecords += invalid.length;
  syncStatus.skippedSyncedRecords += locallySynced.length;

  if (invalid.length > 0) {
    console.log(`${recordType}: skipped ${invalid.length} invalid records`, invalid.slice(0, 5).map(item => item.reason));
  }

  if (valid.length === 0) {
    const state = locallySynced.length > 0 ? 'locally_synced' : 'skipped';
    const details = [];
    if (invalid.length > 0) details.push(`${invalid.length} invalid skipped`);
    if (locallySynced.length > 0) details.push(`${locallySynced.length} already tracked locally`);
    setTypeStatus(recordType, {
      state,
      invalid: invalid.length,
      locallySynced: locallySynced.length,
      error: details.join('; ') || null,
    });
    return 0;
  }

  let uploaded = 0;
  let rejected = [];
  for (const batch of chunkArray(valid, UPLOAD_BATCH_SIZE)) {
    const uploadResult = await uploadBatchIsolatingBadRecords(recordType, batch);
    rejected = [...rejected, ...uploadResult.rejected];
    const uploadedBatch = uploadResult.uploaded;
    uploaded += uploadedBatch.length;
    syncStatus.numRecordsSynced += uploadedBatch.length;
    const daysChanged = await persistSyncedDays(recordType, uploadedBatch);
    syncStatus.syncedDaysUpdated += daysChanged;
    const totalSkipped = invalid.length + locallySynced.length + rejected.length;
    const done = (uploaded + totalSkipped) >= records.length;
    const error = [
      invalid.length > 0 ? `${invalid.length} invalid skipped` : null,
      locallySynced.length > 0 ? `${locallySynced.length} already tracked locally` : null,
      rejected.length > 0 ? `${rejected.length} rejected by server` : null,
    ].filter(Boolean).join('; ') || null;
    setTypeStatus(recordType, {
      synced: uploaded,
      invalid: invalid.length + rejected.length,
      locallySynced: locallySynced.length,
      error,
      state: done ? 'done' : 'syncing',
    });
    updateForegroundProgress(syncStatus.numRecordsSynced, syncStatus.numRecords);
    notifySyncStatus();
  }
  if (rejected.length > 0) {
    console.log(`${recordType}: server rejected ${rejected.length} isolated records`, rejected.slice(0, 5).map(item => item.reason));
  }
  return uploaded;
};

const sync = async (customStartTime, customEndTime) => {
  if (syncInFlight) {
    Toast.show({
      type: 'info',
      text1: 'Sync already running',
      text2: 'The current run will keep uploading before another starts.',
    });
    return syncInFlight;
  }
  syncInFlight = runSync(customStartTime, customEndTime)
    .finally(() => {
      syncInFlight = null;
    });
  return syncInFlight;
};

const runSync = async (customStartTime, customEndTime) => {
  const isInitialized = await initialize();
  if (!isInitialized) {
    Toast.show({
      type: 'error',
      text1: 'Health Connect unavailable',
      text2: 'Install or enable Health Connect, then retry.',
    });
    return;
  }
  console.log("Syncing data...");
  Toast.show({
    type: 'info',
    text1: customStartTime ? "Syncing from custom time..." : "Syncing data...",
  })

  const syncEndTime = customEndTime ? customEndTime : new Date().toISOString();
  const attemptTime = new Date().toISOString();
  lastSyncAttemptAt = attemptTime;
  await setPlain('lastSyncAttemptAt', attemptTime);

  // Start of the full-history window: historyDays back from now.
  const historyStart = String(new Date(new Date().setDate(new Date().getDate() - historyDays)).toISOString());

  // Reading data older than 30 days requires the READ_HEALTH_DATA_HISTORY
  // permission. Ask for it whenever the requested window reaches past 30 days
  // (full-history sync with historyDays > 30, or a custom range older than 30d).
  const daysAgo = (iso) => (Date.now() - new Date(iso).getTime()) / (1000 * 60 * 60 * 24);
  const needsHistory =
    (customStartTime && daysAgo(customStartTime) > 30) ||
    (!customStartTime && fullSyncMode && historyDays > 30);
  let historyPermissionGranted = null;
  if (needsHistory) {
    const granted = await requestHistoryPermission();
    historyPermissionGranted = granted;
    if (!granted) {
      Toast.show({
        type: 'error',
        text1: 'History permission not granted',
        text2: 'This run will be limited to the most recent 30 days.',
      });
    }
  }
  const backgroundPermissionGranted = await requestBackgroundPermission();

  let startTime;
  if (customStartTime) {
    startTime = customStartTime;
  } else if (fullSyncMode) {
    startTime = historyStart;
  } else {
    if (lastSync)
      startTime = lastSync;
    else
      startTime = historyStart;
  }

  if (needsHistory && historyPermissionGranted === false) {
    const thirtyDaysAgo = new Date(Date.now() - (30 * 24 * 60 * 60 * 1000)).toISOString();
    if (new Date(startTime).getTime() < new Date(thirtyDaysAgo).getTime()) {
      startTime = thirtyDaysAgo;
    }
  } else if (!customStartTime && !fullSyncMode && lastSync) {
    startTime = new Date(Math.max(0, new Date(lastSync).getTime() - INCREMENTAL_OVERLAP_MS)).toISOString();
  }

  // Initialize the sync status store for this run.
  syncStatus = {
    running: true,
    startedAt: new Date().toISOString(),
    finishedAt: null,
    phase: 'reading',
    error: null,
    failedTypes: [],
    pagesRead: 0,
    uploadRequests: 0,
    invalidRecords: 0,
    skippedSyncedRecords: 0,
    syncedDaysUpdated: 0,
    windowStart: startTime,
    windowEnd: syncEndTime,
    forceResyncSyncedDays,
    historyPermissionGranted,
    backgroundPermissionGranted,
    lastSuccessfulSyncAt,
    lastAttemptAt: attemptTime,
    numRecords: 0,
    numRecordsSynced: 0,
    currentType: null,
    types: RECORD_TYPES.reduce((acc, t) => { acc[t] = { state: 'pending', count: 0, synced: 0, error: null }; return acc; }, {}),
  };
  notifySyncStatus();

  const grantedReadTypes = await getReadPermissionSet();
  let runSucceeded = true;
  let firstError = null;

  for (let i = 0; i < RECORD_TYPES.length; i++) {
    const recordType = RECORD_TYPES[i];
    if (grantedReadTypes && !grantedReadTypes.has(recordType)) {
      setTypeStatus(recordType, { state: 'permission_missing', error: 'read permission not granted' });
      continue;
    }

    let records;
    syncStatus.currentType = recordType;
    syncStatus.phase = 'reading';
    setTypeStatus(recordType, { state: 'syncing' });
    try {
      console.log(`Reading records for ${recordType} from ${startTime} to ${syncEndTime}`);
      records = await readAllRecords(recordType, {
        operator: "between",
        startTime: startTime,
        endTime: syncEndTime
      });
    }
    catch (err) {
      console.log(err)
      const error = formatSyncError(err);
      runSucceeded = false;
      firstError = firstError || error;
      syncStatus.failedTypes = [...new Set([...syncStatus.failedTypes, recordType])];
      setTypeStatus(recordType, { state: 'failed', error });
      continue;
    }

    console.log(recordType);

    try {
      syncStatus.phase = 'uploading';
      notifySyncStatus();
      await uploadRecords(recordType, records);
    }
    catch (err) {
      console.log(err)
      const error = formatSyncError(err);
      runSucceeded = false;
      firstError = firstError || error;
      syncStatus.failedTypes = [...new Set([...syncStatus.failedTypes, recordType])];
      setTypeStatus(recordType, { state: 'failed', error });
    }
  }

  if (runSucceeded) {
    lastSync = syncEndTime;
    lastSuccessfulSyncAt = syncEndTime;
    lastSyncError = null;
    await setPlain('lastSync', syncEndTime);
    await setPlain('lastSuccessfulSyncAt', syncEndTime);
    await delkey('lastSyncError');
    Toast.show({
      type: 'success',
      text1: 'Sync completed',
      text2: `${syncStatus.numRecordsSynced} records uploaded.`,
    });
    refreshSyncInventory()
      .then(() => notifySyncStatus())
      .catch(err => console.log('post-sync inventory refresh failed', err));
  } else {
    lastSyncError = firstError || 'one or more record types failed';
    await setPlain('lastSyncError', lastSyncError);
    Toast.show({
      type: 'error',
      text1: 'Sync completed with errors',
      text2: 'Last successful sync was not advanced.',
    });
  }

  syncStatus.running = false;
  syncStatus.finishedAt = new Date().toISOString();
  syncStatus.phase = runSucceeded ? 'completed' : 'completed_with_errors';
  syncStatus.error = runSucceeded ? null : lastSyncError;
  syncStatus.lastSuccessfulSyncAt = lastSuccessfulSyncAt;
  syncStatus.currentType = null;
  notifySyncStatus();
  resetForegroundMessage();
}

const handlePush = async (message) => {
  const isInitialized = await initialize();

  let data = JSON.parse(message.data);
  console.log(data);

  insertRecords(data)
    .then((ids) => {
      console.log("Records inserted successfully: ", { ids });
    })
    .catch((error) => {
      Notifications.postLocalNotification({
        body: "Error: " + error.message,
        title: `Push failed for ${data[0].recordType}`,
        silent: false,
        category: "Push Errors",
        fireDate: new Date(),
        android_channel_id: 'push-errors',
      });
    })
}

const handleDel = async (message) => {
  const isInitialized = await initialize();

  let data = JSON.parse(message.data);
  console.log(data);

  deleteRecordsByUuids(data.recordType, data.uuids, data.uuids)
  axios.delete(`${apiBase}/api/v2/sync/${data.recordType}`, {
    data: {
      uuid: data.uuids,
    },
    headers: {
      "Authorization": `Bearer ${login}`
    }
  })
}


export default Sentry.wrap(function App() {
  const [, forceUpdate] = React.useReducer(x => x + 1, 0);
  const [form, setForm] = React.useState(null);
  const [showSyncWarning, setShowSyncWarning] = React.useState(false);
  const [customStartDate, setcustomStartDate] = React.useState(new Date());
  const [customEndDate, setcustomEndDate] = React.useState(new Date());
  const [useCustomDates, setUseCustomDates] = React.useState(false);
  const [showDatePickerModal, setShowDatePickerModal] = React.useState(false);
  const defaultCalStyles = useDefaultStyles();
  const [syncStatusView, setSyncStatusView] = React.useState(syncStatus);
  const [forceResyncView, setForceResyncView] = React.useState(forceResyncSyncedDays);
  const [syncedDaysView, setSyncedDaysView] = React.useState(syncedDaysByType);
  const [inventoryView, setInventoryView] = React.useState(syncInventory);

  // Subscribe to the module-level sync status store so the in-app status UI
  // updates live as sync() progresses. We copy into React state (shallow +
  // fresh types object) so React sees a new reference and re-renders.
  React.useEffect(() => {
    syncStatusListener = () => {
      setSyncStatusView({ ...syncStatus, types: { ...syncStatus.types } });
      setSyncedDaysView({ ...syncedDaysByType });
      setInventoryView(syncInventory ? { ...syncInventory } : null);
    };
    return () => { if (syncStatusListener) syncStatusListener = null; };
  }, [])

  React.useEffect(() => {
    get('forceResyncSyncedDays').then(res => {
      if (res !== null) setForceResyncView(res === 'true');
    });
    get(SYNCED_DAYS_KEY).then(res => {
      if (res && typeof res === 'object') setSyncedDaysView(res);
    });
    get('syncInventory').then(res => {
      if (res && typeof res === 'object') setInventoryView(res);
    });
  }, [])

  const loginFunc = async () => {
    Toast.show({
      type: 'info',
      text1: "Logging in...",
      autoHide: false
    })

    try {
      let fcmToken = await requestUserPermission();
      form.fcmToken = fcmToken;
      let response = await axios.post(`${apiBase}/api/v2/login`, form);
      if ('token' in response.data) {
        console.log(response.data);
        await setPlain('login', response.data.token);
        login = response.data.token;
        await setPlain('refreshToken', response.data.refresh);
        forceUpdate();
        Toast.hide();
        Toast.show({
          type: 'success',
          text1: "Logged in successfully",
        })
        askForPermissions();
      }
      else {
        Toast.hide();
        Toast.show({
          type: 'error',
          text1: "Login failed",
          text2: response.data.error
        })
      }
    }

    catch (err) {
      Toast.hide();
      Toast.show({
        type: 'error',
        text1: "Login failed",
        text2: err.message
      })
    }
  }

  React.useEffect(() => {
    requestNotifications(['alert']).then(({ status, settings }) => {
      console.log(status, settings)
    });

    get('login')
      .then(res => {
        if (res) {
          login = res;
          get('taskDelay')
            .then(res => {
              if (res) taskDelay = Number(res);
            })

          if (!foregroundTasksRegistered) {
            ReactNativeForegroundService.add_task(() => sync(), {
              delay: taskDelay,
              onLoop: true,
              taskId: 'hcgateway_sync',
              onError: e => console.log(`Error logging:`, e),
            });

            ReactNativeForegroundService.add_task(() => refreshTokenFunc(), {
              delay: 10800 * 1000,
              onLoop: true,
              taskId: 'refresh_token',
              onError: e => console.log(`Error logging:`, e),
            });

            foregroundTasksRegistered = true;
          }

          ReactNativeForegroundService.start({
            id: 1244,
            title: 'HCGateway Sync Service',
            message: 'HCGateway is working in the background to sync your data.',
            icon: 'ic_launcher',
            setOnlyAlertOnce: true,
            color: '#000000',
          }).then(() => console.log('Foreground service started'));

          forceUpdate()
        }
      })
  }, [login])

  const formatDateToISOString = (date) => {
    if (!date) return null;
    const midnightDate = new Date(date);
    midnightDate.setHours(0, 0, 0, 0);
    return midnightDate.toISOString();
  };

  const formatDateToReadable = (date) => {
    if (!date) return 'Not selected';
    return date.toLocaleDateString();
  };

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.screenContent} keyboardShouldPersistTaps="handled">
      {login &&
        <View style={styles.panel}>
          <Text style={{ fontSize: 20, marginVertical: 10 }}>You are currently logged in.</Text>
          <Text style={{ fontSize: 17, marginVertical: 10 }}>Last successful sync: {lastSuccessfulSyncAt || lastSync || 'Never'}</Text>
          <Text style={styles.statusMetaText}>Last attempt: {lastSyncAttemptAt || syncStatusView.lastAttemptAt || 'Never'}</Text>
          {!!lastSyncError && <Text style={styles.errorText}>Last error: {lastSyncError}</Text>}

          {/* ---- Sync status panel: per-record-type breakdown ---- */}
          <View style={styles.statusPanel}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text style={styles.statusHeader}>Sync Status</Text>
              <Text style={styles.statusState}>
                {syncStatusView.running
                  ? `${syncStatusView.phase === 'uploading' ? 'Uploading' : 'Reading'}${syncStatusView.currentType ? ` ${syncStatusView.currentType}...` : '...'}`
                  : (syncStatusView.finishedAt ? (syncStatusView.error ? 'Idle (last run had errors)' : 'Idle (last run complete)') : 'Idle')}
              </Text>
            </View>

            <View style={styles.statusMeta}>
              <Text style={styles.statusMetaText}>Window: {syncStatusView.windowStart || '-'} to {syncStatusView.windowEnd || '-'}</Text>
              <Text style={styles.statusMetaText}>Pages read: {syncStatusView.pagesRead || 0} | Upload requests: {syncStatusView.uploadRequests || 0}</Text>
              <Text style={styles.statusMetaText}>Invalid skipped: {syncStatusView.invalidRecords || 0} | Locally skipped: {syncStatusView.skippedSyncedRecords || 0}</Text>
              <Text style={styles.statusMetaText}>Local days updated: {syncStatusView.syncedDaysUpdated || 0} | Force re-upload: {syncStatusView.forceResyncSyncedDays ? 'on' : 'off'}</Text>
              <Text style={styles.statusMetaText}>History access: {syncStatusView.historyPermissionGranted === null ? 'not needed' : (syncStatusView.historyPermissionGranted ? 'granted' : 'limited to 30 days')}</Text>
              <Text style={styles.statusMetaText}>Background read: {syncStatusView.backgroundPermissionGranted === null ? 'unknown' : (syncStatusView.backgroundPermissionGranted ? 'granted' : 'not granted')}</Text>
              {(syncStatusView.failedTypes || []).length > 0 && (
                <Text style={styles.errorText}>Failed types: {syncStatusView.failedTypes.join(', ')}</Text>
              )}
            </View>

            {(syncStatusView.numRecords > 0) && (
              <View style={{ marginTop: 6 }}>
                <Text style={{ fontSize: 13, color: '#555' }}>
                  {syncStatusView.numRecordsSynced}/{syncStatusView.numRecords} records
                </Text>
                <View style={styles.progressTrack}>
                  <View style={[styles.progressFill, { width: `${Math.min(100, Math.round((syncStatusView.numRecordsSynced / Math.max(1, syncStatusView.numRecords)) * 100))}%` }]} />
                </View>
              </View>
            )}

            <View style={styles.typeList}>
              {Object.keys(syncStatusView.types).map((t) => {
                const info = syncStatusView.types[t];
                const badge = STATUS_BADGE[info.state] || STATUS_BADGE.pending;
                return (
                  <View key={t} style={styles.typeRow}>
                    <Text style={styles.typeName} numberOfLines={1}>{t}</Text>
                    <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                      {info.count > 0 && (
                        <Text style={styles.typeCount}>{info.synced}/{info.count}</Text>
                      )}
                      {(info.invalid || info.locallySynced) > 0 && (
                        <Text style={styles.typeCount}>
                          {info.invalid ? ` bad ${info.invalid}` : ''}{info.locallySynced ? ` local ${info.locallySynced}` : ''}
                        </Text>
                      )}
                      <View style={[styles.badge, { backgroundColor: badge.color }]}>
                        <Text style={styles.badgeText}>{badge.label}</Text>
                      </View>
                    </View>
                  </View>
                );
              })}
            </View>
          </View>

          <View style={styles.statusPanel}>
            <Text style={styles.statusHeader}>Synced Coverage</Text>
            <Text style={styles.statusMetaText}>Local tracked days: {trackedDaysSummary(syncedDaysView)}</Text>
            <Text style={styles.statusMetaText}>Tracked record types: {Object.keys(syncedDaysView || {}).length}</Text>
            {inventoryView && (
              <View style={styles.statusMeta}>
                <Text style={styles.statusMetaText}>Server inventory: {inventoryView.totalRecords || 0} records</Text>
                <Text style={styles.statusMetaText}>Server range: {inventoryView.earliest || '-'} to {inventoryView.latest || '-'}</Text>
                <Text style={styles.statusMetaText}>Inventory fetched: {inventoryView.fetchedAt || 'unknown'}</Text>
                {!!inventoryView.signals?.steps && (
                  <Text style={styles.statusMetaText}>Steps on server: {inventoryView.signals.steps.records} records, {inventoryView.signals.steps.earliest || '-'} to {inventoryView.signals.steps.latest || '-'}</Text>
                )}
              </View>
            )}
            <View style={{ marginTop: 8 }}>
              <Button
                title="Refresh Server Inventory"
                disabled={syncStatusView.running}
                onPress={async () => {
                  try {
                    const inventory = await refreshSyncInventory();
                    setInventoryView(inventory);
                    Toast.show({
                      type: 'success',
                      text1: 'Server inventory refreshed',
                      text2: `${inventory.totalRecords || 0} raw records on server.`,
                    });
                  } catch (err) {
                    Toast.show({
                      type: 'error',
                      text1: 'Inventory refresh failed',
                      text2: err.message,
                    });
                  }
                }}
              />
            </View>
          </View>

          <Text style={{ marginTop: 10, fontSize: 15 }}>API Base URL:</Text>
          <TextInput
            style={styles.input}
            placeholder="API Base URL"
            defaultValue={apiBase}
            onChangeText={text => {
              apiBase = text;
              setPlain('apiBase', text);
            }}
          />

          <Text style={{ marginTop: 10, fontSize: 15 }}>Sync Interval (in hours):</Text>
          <TextInput
            style={styles.input}
            placeholder="Sync Interval"
            keyboardType='numeric'
            defaultValue={(taskDelay / (1000 * 60 * 60)).toString()}
            onChangeText={text => {
              const hours = Number(text);
              taskDelay = hours * 60 * 60 * 1000;
              setPlain('taskDelay', String(taskDelay));
              ReactNativeForegroundService.update_task(() => sync(), {
                taskId: 'hcgateway_sync',
                delay: taskDelay,
              })
              Toast.show({
                type: 'success',
                text1: `Sync interval updated to ${hours} ${hours === 1 ? 'hour' : 'hours'}`,
              })
            }}
          />

          <View style={{ flexDirection: 'row', alignItems: 'center', marginVertical: 10 }}>
            <Text style={{ fontSize: 15 }}>Enable Sentry:</Text>
            <Switch
              value={isSentryEnabled}
              onValueChange={async (value) => {
                if (value) {
                  Sentry.init({
                    dsn: '',
                    tracesSampleRate: 1.0,
                  });
                  Toast.show({
                    type: 'success',
                    text1: "Sentry enabled",
                  });
                  isSentryEnabled = true;
                  forceUpdate();
                } else {
                  Sentry.close();
                  Toast.show({
                    type: 'success',
                    text1: "Sentry disabled",
                  });
                  isSentryEnabled = false;
                  forceUpdate();
                }
                await setPlain('sentryEnabled', value.toString());
              }}
            />
          </View>

          <View style={{ flexDirection: 'row', alignItems: 'center', marginVertical: 10 }}>
            <Text style={{ fontSize: 15 }}>Full history sync:</Text>
            <Switch
              value={fullSyncMode}
              onValueChange={async (value) => {
                if (!value) {
                  setShowSyncWarning(true);
                } else {
                  fullSyncMode = value;
                  await setPlain('fullSyncMode', value.toString());
                  Toast.show({
                    type: 'info',
                    text1: "Sync mode updated",
                    text2: `Will sync the last ${historyDays} days of data`
                  });
                  forceUpdate();
                }
              }}
            />
          </View>

          <View style={{ flexDirection: 'row', alignItems: 'center', marginVertical: 10 }}>
            <Text style={{ fontSize: 15, flex: 1 }}>Force re-upload locally tracked days:</Text>
            <Switch
              value={forceResyncView}
              onValueChange={async (value) => {
                forceResyncSyncedDays = value;
                setForceResyncView(value);
                await setPlain('forceResyncSyncedDays', value.toString());
                Toast.show({
                  type: 'info',
                  text1: value ? 'Force re-upload enabled' : 'Local day skipping enabled',
                  text2: value ? 'Syncs will slam matching days into the server again.' : 'Already tracked days can be skipped locally.',
                });
                forceUpdate();
              }}
            />
          </View>

          <View style={{ marginBottom: 8 }}>
            <Button
              title="Reset Local Day Tracker"
              color="#795548"
              disabled={syncStatusView.running || countTrackedDays(syncedDaysView) === 0}
              onPress={async () => {
                syncedDaysByType = {};
                setSyncedDaysView({});
                await setObj(SYNCED_DAYS_KEY, {});
                Toast.show({
                  type: 'success',
                  text1: 'Local day tracker reset',
                  text2: 'The next sync can rebuild local coverage from uploads.',
                });
              }}
            />
          </View>

          <Text style={{ marginTop: 10, fontSize: 15 }}>History window (days back):</Text>
          <TextInput
            style={styles.input}
            placeholder="Days of history"
            keyboardType='numeric'
            defaultValue={String(historyDays)}
            onChangeText={text => {
              const days = Number(text);
              if (isNaN(days) || days < 1) return;
              historyDays = days;
              setPlain('historyDays', String(days));
            }}
          />
          {historyDays > 30 && (
            <Text style={{ fontSize: 12, color: '#a06a00', marginBottom: 4 }}>
              Reading past 30 days needs the "access past data" permission in
              Health Connect. You'll be prompted on the next full-history sync.
            </Text>
          )}

          <View style={{ marginTop: 6, marginBottom: 4 }}>
            <Button
              title={`Sync Full History (${historyDays} days)`}
              color="#6a1b9a"
              disabled={syncStatusView.running}
              onPress={async () => {
                if (historyDays > 30) {
                  const granted = await requestHistoryPermission();
                  if (!granted) {
                    Toast.show({
                      type: 'error',
                      text1: 'History permission not granted',
                      text2: 'Enable "access past data" for HCGateway in Health Connect, then retry.',
                    });
                    return;
                  }
                }
                const start = String(new Date(new Date().setDate(new Date().getDate() - historyDays)).toISOString());
                sync(start, new Date().toISOString());
              }}
            />
          </View>

          {showSyncWarning && (
            <View style={styles.warningContainer}>
              <Text style={styles.warningText}>
                Warning: Incremental sync only syncs data since the last sync.
                You may miss data if the app stops abruptly.
              </Text>
              <View style={styles.warningButtons}>
                <Button
                  title="Cancel"
                  onPress={() => {
                    setShowSyncWarning(false);
                  }}
                />
                <Button
                  title="Continue"
                  onPress={async () => {
                    fullSyncMode = false;
                    await setPlain('fullSyncMode', 'false');
                    setShowSyncWarning(false);
                    Toast.show({
                      type: 'info',
                      text1: "Sync mode updated",
                      text2: "Will only sync data since last sync"
                    });
                    forceUpdate();
                  }}
                />
              </View>
            </View>
          )}

          <View style={{ marginTop: 10, marginBottom: 5 }}>
            <Text style={{ fontSize: 15, marginBottom: 5 }}>Sync Range:</Text>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text>
                {customStartDate ? formatDateToReadable(customStartDate) : 'Not set'} -
                {customEndDate ? formatDateToReadable(customEndDate) : 'Not set'}
              </Text>
              <Button
                title="Select Dates"
                onPress={() => setShowDatePickerModal(true)}
              />
            </View>
          </View>

          <Modal
            visible={showDatePickerModal}
            transparent={true}
            animationType="slide"
            onRequestClose={() => setShowDatePickerModal(false)}
          >
            <View style={styles.modalOverlay}>
              <View style={styles.modalContent}>
                <Text style={styles.modalTitle}>Select Date Range</Text>

                <DateTimePicker
                  mode="range"
                  maxDate={new Date()}
                  startDate={customStartDate}
                  endDate={customEndDate}
                  onChange={(...dates) => {
                    setUseCustomDates(true);
                    if (dates[0].startDate) setcustomStartDate(dates[0].startDate);
                    if (dates[0].endDate) setcustomEndDate(dates[0].endDate);
                  }}
                  styles={defaultCalStyles}
                />

                <View style={styles.modalButtons}>
                  <Button
                    title="Cancel"
                    onPress={() => setShowDatePickerModal(false)}
                    color="darkgrey"
                  />
                  <Button
                    title="Apply"
                    onPress={() => {
                      setUseCustomDates(true);
                      setShowDatePickerModal(false);
                    }}
                  />
                </View>
              </View>
            </View>
          </Modal>

          <View style={{ marginTop: 10, marginBottom: 10 }}>
            <Button
              title={useCustomDates ? "Sync Selected Range" : "Sync Now (Default)"}
              disabled={syncStatusView.running}
              onPress={() => {
                if (!useCustomDates) {
                  sync();
                }
                else if (customStartDate && customEndDate) {
                  sync(formatDateToISOString(customStartDate), formatDateToISOString(customEndDate));
                }
              }}
            />
          </View>

          <View style={{ marginTop: 20 }}>
            <Button
              title="Logout"
              onPress={() => {
                delkey('login');
                login = null;
                Toast.show({
                  type: 'success',
                  text1: "Logged out successfully",
                })
                forceUpdate();
              }}
              color={'darkred'}
            />
          </View>
        </View>
      }
      {!login &&
        <View style={styles.panel}>
          <Text style={{
            fontSize: 30,
            fontWeight: 'bold',
            textAlign: 'center',
          }}>Login</Text>

          <Text style={{ marginVertical: 10 }}>If you don't have an account, one will be made for you when logging in.</Text>

          <TextInput
            style={styles.input}
            placeholder="Username"
            onChangeText={text => setForm({ ...form, username: text })}
          />
          <TextInput
            style={styles.input}
            placeholder="Password"
            secureTextEntry={true}
            onChangeText={text => setForm({ ...form, password: text })}
          />
          <Text style={{ marginVertical: 10 }}>API Base URL:</Text>
          <TextInput
            style={styles.input}
            placeholder="API Base URL"
            defaultValue={apiBase}
            onChangeText={text => {
              apiBase = text;
              setPlain('apiBase', text);
            }}
          />

          <View style={{ flexDirection: 'row', alignItems: 'center', marginVertical: 10 }}>
            <Text style={{ fontSize: 15 }}>Enable Sentry:</Text>
            <Switch
              value={isSentryEnabled}
              defaultValue={isSentryEnabled}
              onValueChange={async (value) => {
                if (value) {
                  Sentry.init({
                    dsn: '',
                  });
                  Toast.show({
                    type: 'success',
                    text1: "Sentry enabled",
                  });
                  isSentryEnabled = true;
                  forceUpdate();
                } else {
                  Sentry.close();
                  Toast.show({
                    type: 'success',
                    text1: "Sentry disabled",
                  });
                  isSentryEnabled = false;
                  forceUpdate();
                }
                await setPlain('sentryEnabled', value.toString());
              }}
            />
          </View>

          <Button
            title="Login"
            onPress={() => {
              loginFunc()
            }}
          />
        </View>
      }

      <StatusBar style="dark" />
      <Toast />
    </ScrollView>
  );
});;

const styles = StyleSheet.create({
  container: {
    backgroundColor: '#fff',
    width: '100%',
  },

  screenContent: {
    alignItems: 'center',
    padding: 24,
    paddingTop: 44,
    paddingBottom: 60,
  },

  panel: {
    width: '100%',
    maxWidth: 430,
  },

  input: {
    height: 50,
    marginVertical: 7,
    borderWidth: 1,
    borderRadius: 4,
    padding: 10,
    width: '100%',
    fontSize: 17
  },

  warningContainer: {
    backgroundColor: '#fff3cd',
    borderColor: '#ffeeba',
    borderWidth: 1,
    borderRadius: 5,
    padding: 10,
    marginVertical: 10,
  },

  warningText: {
    color: '#856404',
    marginBottom: 10,
  },

  warningButtons: {
    flexDirection: 'row',
    justifyContent: 'space-around',
  },

  modalOverlay: {
    flex: 1,
    backgroundColor: 'rgba(0,0,0,0.5)',
    justifyContent: 'center',
    alignItems: 'center',
  },

  modalContent: {
    backgroundColor: 'white',
    borderRadius: 10,
    padding: 20,
    width: '90%',
    maxHeight: '80%',
  },

  modalTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    marginBottom: 15,
    textAlign: 'center',
  },

  modalButtons: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    marginTop: 15,
  },

  statusPanel: {
    borderWidth: 1,
    borderColor: '#e0e0e0',
    borderRadius: 8,
    padding: 12,
    marginVertical: 10,
    backgroundColor: '#fafafa',
  },

  statusMeta: {
    marginTop: 8,
  },

  statusMetaText: {
    fontSize: 12,
    color: '#555',
    marginTop: 2,
  },

  errorText: {
    fontSize: 12,
    color: '#b71c1c',
    marginTop: 4,
  },

  statusHeader: {
    fontSize: 16,
    fontWeight: 'bold',
  },

  statusState: {
    fontSize: 13,
    color: '#555',
  },

  progressTrack: {
    height: 6,
    borderRadius: 3,
    backgroundColor: '#e0e0e0',
    marginTop: 4,
    overflow: 'hidden',
  },

  progressFill: {
    height: 6,
    borderRadius: 3,
    backgroundColor: '#1e88e5',
  },

  typeList: {
    marginTop: 10,
  },

  typeRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 3,
  },

  typeName: {
    fontSize: 13,
    flexShrink: 1,
    marginRight: 8,
  },

  typeCount: {
    fontSize: 12,
    color: '#777',
    marginRight: 6,
  },

  badge: {
    borderRadius: 10,
    paddingHorizontal: 8,
    paddingVertical: 2,
    minWidth: 58,
    alignItems: 'center',
  },

  badgeText: {
    color: 'white',
    fontSize: 11,
    fontWeight: '600',
  },
});
