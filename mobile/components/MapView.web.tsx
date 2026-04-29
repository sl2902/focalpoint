import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, StyleSheet } from 'react-native';
import { SEVERITY_COLORS, SEVERITY_BG_COLORS } from '../constants/severity';
import type { ComponentMarker } from '../types/map';
import type { Severity } from '../types/api';

interface Props {
  markers: ComponentMarker[];
  onMarkerPress: (marker: ComponentMarker) => void;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatUtc(ts: string): string {
  return new Date(ts).toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
}

function stripAnnotations(text: string): string {
  return text.replace(/\[[^\]]+\]/g, '').replace(/\s{2,}/g, ' ').trim();
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max).trimEnd() + '…';
}

// ── Popup HTML (built in TypeScript — never string-interpolated in Leaflet) ──

function buildPopupHtml(marker: ComponentMarker): string {
  if (marker.severity === 'INSUFFICIENT_DATA' || !marker.timestamp) {
    return (
      '<div class="fp-popup">' +
      '<div class="fp-region">' + marker.region + '</div>' +
      '<p class="fp-nodata">No assessment data for this region.<br>Select it as your Watch Zone in Settings to load.</p>' +
      '</div>'
    );
  }

  const color   = SEVERITY_COLORS[marker.severity as Severity];
  const bgColor = SEVERITY_BG_COLORS[marker.severity as Severity];
  const ts      = formatUtc(marker.timestamp);
  const summary = marker.summary
    ? truncate(stripAnnotations(marker.summary), 100)
    : '';
  const conf = marker.confidence !== undefined
    ? Math.round(marker.confidence * 100) + '%'
    : '—';

  return (
    '<div class="fp-popup">' +
    '<div class="fp-header">' +
    '<span class="fp-badge" style="background:' + bgColor + ';color:' + color + '">' +
    '<span class="fp-badge-dot" style="background:' + color + '"></span>' +
    marker.severity +
    '</span>' +
    '<span class="fp-region">' + marker.region + '</span>' +
    '</div>' +
    '<div class="fp-ts">' + ts + '</div>' +
    (summary ? '<div class="fp-summary">' + summary + '</div>' : '') +
    '<div class="fp-conf">Confidence ' + conf + '</div>' +
    '<button class="fp-btn" data-mid="' + marker.id + '" onclick="fpNavigate(this.getAttribute(\'data-mid\'))">View Full Assessment →</button>' +
    '</div>'
  );
}

// ── Marker data payload (sent to iframe via postMessage) ──────────────────────

interface MarkerDatum {
  id: string;
  lat: number;
  lng: number;
  color: string;
  severity: string;
  isCritical: boolean;
  popupHtml: string;
}

function buildMarkerData(markers: ComponentMarker[]): MarkerDatum[] {
  return markers.map((m) => ({
    id:         m.id,
    lat:        m.latitude,
    lng:        m.longitude,
    color:      (SEVERITY_COLORS[m.severity as Severity] ?? '#6b7280') as string,
    severity:   m.severity,
    isCritical: m.severity === 'CRITICAL',
    popupHtml:  buildPopupHtml(m),
  }));
}

// ── Leaflet base HTML — loaded once, markers injected via postMessage ─────────
//
// The iframe never reloads after the initial load. When the parent's markers
// state changes it sends a "fpUpdateMarkers" postMessage; the iframe clears its
// cluster group and re-adds the new markers without destroying the Leaflet
// instance or the map viewport.

const LEAFLET_BASE_HTML = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.css" />
  <style>
    html, body, #map { height: 100%; margin: 0; padding: 0; background: #1a1a2e; }

    /* ── CRITICAL pulse ───────────────────────────────────── */
    .fp-crit-icon { background: transparent !important; border: none !important; }
    .fp-crit-wrap {
      width: 50px; height: 50px;
      display: flex; align-items: center; justify-content: center;
      position: relative;
    }
    .fp-crit-dot {
      position: absolute;
      width: 14px; height: 14px; border-radius: 50%;
      border: 2px solid #fff;
      z-index: 2;
    }
    .fp-crit-ring {
      position: absolute;
      width: 14px; height: 14px; border-radius: 50%;
      border: 2.5px solid;
      opacity: 0;
      animation: fp-pulse 2s ease-out infinite;
    }
    .fp-crit-ring-2 { animation-delay: 1s; }
    @keyframes fp-pulse {
      0%   { transform: scale(1);   opacity: 0.75; }
      75%  { transform: scale(3.2); opacity: 0;    }
      100% { transform: scale(3.2); opacity: 0;    }
    }

    /* ── Cluster icons ────────────────────────────────────── */
    .fp-cluster-icon { background: transparent !important; border: none !important; }
    .fp-cluster {
      width: 36px; height: 36px; border-radius: 50%;
      display: flex; align-items: center; justify-content: center;
      color: #fff; font-size: 13px; font-weight: 700;
      border: 2px solid rgba(255,255,255,0.45);
      box-shadow: 0 2px 10px rgba(0,0,0,0.5);
    }

    /* ── Popup shell ──────────────────────────────────────── */
    .leaflet-popup-content-wrapper {
      background: #16213e !important;
      border-radius: 10px !important;
      padding: 0 !important;
      box-shadow: 0 4px 24px rgba(0,0,0,0.6) !important;
      border: 1px solid rgba(255,255,255,0.08) !important;
    }
    .leaflet-popup-tip { background: #16213e !important; }
    .leaflet-popup-content { margin: 0 !important; padding: 0 !important; }
    .leaflet-popup-close-button { color: #6b7280 !important; top: 8px !important; right: 8px !important; }
    .leaflet-popup-close-button:hover { color: #e5e7eb !important; background: transparent !important; }

    /* ── Popup content ────────────────────────────────────── */
    .fp-popup { padding: 12px 14px 14px; font-family: -apple-system, BlinkMacSystemFont, sans-serif; color: #e5e7eb; width: 230px; }
    .fp-header { display: flex; align-items: center; gap: 7px; margin-bottom: 5px; }
    .fp-badge { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 10px; font-size: 10px; font-weight: 700; letter-spacing: 0.4px; text-transform: uppercase; white-space: nowrap; flex-shrink: 0; }
    .fp-badge-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
    .fp-region { font-weight: 700; font-size: 14px; color: #f9fafb; }
    .fp-ts { font-size: 11px; color: #6b7280; margin-bottom: 5px; }
    .fp-summary { font-size: 12px; color: #d1d5db; line-height: 1.5; margin-bottom: 5px; }
    .fp-conf { font-size: 11px; color: #9ca3af; margin-bottom: 10px; }
    .fp-btn { display: block; width: 100%; background: #2563eb; color: #fff; border: none; border-radius: 6px; padding: 7px 0; font-size: 12px; font-weight: 600; cursor: pointer; text-align: center; box-sizing: border-box; }
    .fp-btn:hover { background: #1d4ed8; }
    .fp-nodata { font-size: 12px; color: #6b7280; line-height: 1.5; margin: 4px 0 0; }
  </style>
</head>
<body>
  <div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/leaflet.markercluster.js"></script>
  <script>
    var map = L.map('map').setView([31.9, 35.2], 3);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
      subdomains: 'abcd',
      maxZoom: 20
    }).addTo(map);

    function fpNavigate(id) {
      window.parent.postMessage({ type: 'fpMarkerPress', id: id }, '*');
    }

    var SEV_ORDER  = ['CRITICAL', 'RED', 'AMBER', 'GREEN', 'INSUFFICIENT_DATA'];
    var SEV_COLORS = {
      CRITICAL: '#7c3aed', RED: '#ef4444', AMBER: '#f59e0b',
      GREEN: '#22c55e', INSUFFICIENT_DATA: '#6b7280'
    };

    function highestSeverity(childMarkers) {
      var best = SEV_ORDER.length - 1;
      childMarkers.forEach(function(m) {
        var idx = SEV_ORDER.indexOf(m.fpSeverity || 'INSUFFICIENT_DATA');
        if (idx < best) best = idx;
      });
      return SEV_ORDER[best];
    }

    var clusterGroup = L.markerClusterGroup({
      showCoverageOnHover: false,
      maxClusterRadius: 60,
      iconCreateFunction: function(cluster) {
        var children = cluster.getAllChildMarkers();
        var sev   = highestSeverity(children);
        var color = SEV_COLORS[sev];
        var count = cluster.getChildCount();
        return L.divIcon({
          html: '<div class="fp-cluster" style="background:' + color + '">' + count + '</div>',
          className: 'fp-cluster-icon',
          iconSize: [36, 36],
          iconAnchor: [18, 18]
        });
      }
    });
    map.addLayer(clusterGroup);

    function addMarker(m) {
      var leafletMarker;

      if (m.isCritical) {
        var critHtml =
          '<div class="fp-crit-wrap">' +
          '<div class="fp-crit-ring" style="border-color:' + m.color + '"></div>' +
          '<div class="fp-crit-ring fp-crit-ring-2" style="border-color:' + m.color + '"></div>' +
          '<div class="fp-crit-dot" style="background:' + m.color + '"></div>' +
          '</div>';
        var icon = L.divIcon({
          className: 'fp-crit-icon',
          html: critHtml,
          iconSize: [50, 50],
          iconAnchor: [25, 25],
          popupAnchor: [0, -28]
        });
        leafletMarker = L.marker([m.lat, m.lng], { icon: icon });
      } else {
        leafletMarker = L.circleMarker([m.lat, m.lng], {
          radius: 8,
          fillColor: m.color,
          color: '#ffffff',
          weight: 2,
          opacity: 1,
          fillOpacity: 0.9
        });
      }

      leafletMarker.fpSeverity = m.severity;
      leafletMarker.bindPopup(m.popupHtml, { maxWidth: 250, closeButton: true });
      leafletMarker.on('click', function() { leafletMarker.openPopup(); });
      clusterGroup.addLayer(leafletMarker);
    }

    window.addEventListener('message', function(event) {
      if (!event.data || event.data.type !== 'fpUpdateMarkers') return;
      clusterGroup.clearLayers();
      event.data.markers.forEach(addMarker);
    });
  </script>
</body>
</html>`;

// ── Component ─────────────────────────────────────────────────────────────────

export default function MapViewWeb({ markers, onMarkerPress }: Props) {
  const iframeRef        = useRef<HTMLIFrameElement | null>(null);
  const onMarkerPressRef = useRef(onMarkerPress);
  onMarkerPressRef.current = onMarkerPress;
  const markersRef = useRef(markers);
  markersRef.current = markers;

  // State (not ref) so that when the iframe loads it triggers a re-render and
  // the send-markers effect runs with the guaranteed-current markers value.
  const [iframeReady, setIframeReady] = useState(false);

  function pushMarkers(ms: ComponentMarker[]) {
    const iframe = iframeRef.current;
    if (!iframe?.contentWindow) return;
    iframe.contentWindow.postMessage(
      { type: 'fpUpdateMarkers', markers: buildMarkerData(ms) },
      '*',
    );
  }

  // Called once when the iframe finishes loading its base HTML + Leaflet scripts.
  const handleLoad = useCallback(() => {
    setIframeReady(true);
  }, []);

  // Send (or re-send) markers whenever the iframe becomes ready OR markers change.
  // Using iframeReady as state means the effect sees the committed React markers
  // value — no ref timing race between the iframe load event and React commits.
  useEffect(() => {
    if (!iframeReady) return;
    pushMarkers(markers);
  }, [iframeReady, markers]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.data?.type !== 'fpMarkerPress') return;
      const marker = markersRef.current.find((m) => m.id === event.data.id);
      if (marker) onMarkerPressRef.current(marker);
    }
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, []);

  return (
    <View style={styles.container}>
      {React.createElement('iframe', {
        ref:     iframeRef,
        onLoad:  handleLoad,
        title:   'Incident Map',
        srcDoc:  LEAFLET_BASE_HTML,
        sandbox: 'allow-scripts',
        style:   { border: 'none', width: '100%', height: '100%', display: 'block' },
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
});
