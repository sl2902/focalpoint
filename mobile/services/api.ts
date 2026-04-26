/**
 * Base fetch wrapper for all backend requests.
 *
 * Responsibilities:
 * - Reads EXPO_PUBLIC_API_URL from env
 * - Generates and persists a device_id (required by backend rate limiter)
 * - Injects Accept-Language header from stored preference
 * - Appends `days` query param to GET requests when provided
 */

import { getItem, setItem } from './storage';

const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000';

const DEVICE_ID_KEY = 'focalpoint_device_id';
const LANGUAGE_KEY  = 'focalpoint_language';

let _deviceId: string | null = null;

async function getDeviceId(): Promise<string> {
  if (_deviceId) return _deviceId;
  const stored = await getItem(DEVICE_ID_KEY);
  if (stored) {
    _deviceId = stored;
    return stored;
  }
  const id = `fp-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  await setItem(DEVICE_ID_KEY, id);
  _deviceId = id;
  return id;
}

export async function getStoredLanguage(): Promise<string> {
  return (await getItem(LANGUAGE_KEY)) ?? 'en';
}

export async function setStoredLanguage(lang: string): Promise<void> {
  await setItem(LANGUAGE_KEY, lang);
}

interface GetOptions {
  params?: Record<string, string | number | boolean>;
}

export async function apiGet<T>(path: string, options: GetOptions = {}): Promise<T> {
  const deviceId = await getDeviceId();
  const language = await getStoredLanguage();

  const url = new URL(`${BASE_URL}${path}`);
  if (options.params) {
    for (const [key, value] of Object.entries(options.params)) {
      url.searchParams.set(key, String(value));
    }
  }

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      'Accept-Language': language,
      'device_id': deviceId,
    },
  });

  if (!response.ok) {
    throw new Error(`API error ${response.status}: ${await response.text()}`);
  }

  return response.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body: FormData): Promise<T> {
  const deviceId = await getDeviceId();
  const language = await getStoredLanguage();

  // Do NOT set Content-Type — fetch sets multipart/form-data + boundary automatically
  const response = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      'Accept-Language': language,
      'device_id': deviceId,
    },
    body,
  });

  if (!response.ok) {
    throw new Error(`API error ${response.status}: ${await response.text()}`);
  }

  return response.json() as Promise<T>;
}
