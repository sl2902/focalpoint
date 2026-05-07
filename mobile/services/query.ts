import { apiPost } from './api';
import type { QueryResponse, TranscribeResponse } from '../types/api';

export interface QueryParams {
  region: string;
  text: string;
  language?: string;
}

/**
 * POST /query — multipart/form-data, text only.
 * Audio must be transcribed via POST /transcribe first; never send audio bytes here.
 * Do NOT set Content-Type manually — fetch sets multipart boundary automatically.
 */
export async function postQuery(params: QueryParams): Promise<QueryResponse> {
  const form = new FormData();
  form.append('region', params.region);
  form.append('text', params.text);

  if (params.language) {
    form.append('language', params.language);
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
