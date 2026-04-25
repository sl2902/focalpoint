import type { Severity } from '../types/api';

export const SEVERITY_COLORS: Record<Severity, string> = {
  GREEN:             '#22c55e',
  AMBER:             '#f59e0b',
  RED:               '#ef4444',
  CRITICAL:          '#7c3aed',
  INSUFFICIENT_DATA: '#6b7280',
};

export const SEVERITY_BG_COLORS: Record<Severity, string> = {
  GREEN:             '#f0fdf4',
  AMBER:             '#fffbeb',
  RED:               '#fef2f2',
  CRITICAL:          '#f5f3ff',
  INSUFFICIENT_DATA: '#f9fafb',
};

// Severity order for sorting: higher index = more severe
export const SEVERITY_ORDER: Record<Severity, number> = {
  INSUFFICIENT_DATA: -1,
  GREEN:              0,
  AMBER:              1,
  RED:                2,
  CRITICAL:           3,
};
