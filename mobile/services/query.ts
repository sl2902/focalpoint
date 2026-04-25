import { apiPost } from './api';
import type { QueryResponse, TranscribeResponse } from '../types/api';

export interface QueryParams {
  region: string;
  text?: string;
  language?: string;
  audioUri?: string;  // local file URI from Expo AV recording
}

/**
 * POST /query — multipart/form-data.
 * Either `text` or `audioUri` (or both) must be provided.
 * Do NOT set Content-Type manually — fetch sets multipart boundary automatically.
 */
export async function postQuery(params: QueryParams): Promise<QueryResponse> {
  const form = new FormData();
  form.append('region', params.region);

  if (params.text) {
    form.append('text', params.text);
  }
  if (params.language) {
    form.append('language', params.language);
  }
  if (params.audioUri) {
    // React Native FormData expects { uri, name, type } for file fields
    form.append('audio', {
      uri: params.audioUri,
      name: 'audio.m4a',
      type: 'audio/m4a',
    } as unknown as Blob);
  }

  return apiPost<QueryResponse>('/query', form);
}

export interface TranscribeParams {
  audioUri: string;
  language?: string;
}

/**
 * POST /transcribe — multipart/form-data.
 * Returns the transcribed text so the Query screen can display it
 * before (or instead of) calling /query.
 */
export async function postTranscribe(
  params: TranscribeParams,
): Promise<TranscribeResponse> {
  const form = new FormData();
  form.append('audio', {
    uri: params.audioUri,
    name: 'audio.m4a',
    type: 'audio/m4a',
  } as unknown as Blob);

  if (params.language) {
    form.append('language', params.language);
  }

  return apiPost<TranscribeResponse>('/transcribe', form);
}
