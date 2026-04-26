import React, { useEffect, useMemo, useRef } from 'react';
import { View, StyleSheet } from 'react-native';
import { SEVERITY_COLORS } from '../constants/severity';
import type { ComponentMarker } from '../types/map';

interface Props {
  markers: ComponentMarker[];
  onMarkerPress: (marker: ComponentMarker) => void;
}

function buildLeafletHtml(markers: ComponentMarker[]): string {
  const markerData = markers.map((m) => ({
    id: m.id,
    lat: m.latitude,
    lng: m.longitude,
    color: SEVERITY_COLORS[m.severity],
    region: m.region,
  }));

  return `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <style>
    html, body, #map { height: 100%; margin: 0; padding: 0; background: #1a1a2e; }
  </style>
</head>
<body>
  <div id="map"></div>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    var map = L.map('map').setView([31.9, 35.2], 3);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: 'abcd',
      maxZoom: 20
    }).addTo(map);

    var markers = ${JSON.stringify(markerData)};
    markers.forEach(function(m) {
      var circle = L.circleMarker([m.lat, m.lng], {
        radius: 8,
        fillColor: m.color,
        color: '#ffffff',
        weight: 2,
        opacity: 1,
        fillOpacity: 0.9
      }).addTo(map);
      circle.bindTooltip(m.region, { permanent: false, direction: 'top' });
      circle.on('click', function() {
        window.parent.postMessage(
          { type: 'fpMarkerPress', id: m.id, region: m.region },
          '*'
        );
      });
    });
  </script>
</body>
</html>`;
}

export default function MapViewWeb({ markers, onMarkerPress }: Props) {
  const html = useMemo(() => buildLeafletHtml(markers), [markers]);
  const markersRef = useRef(markers);
  markersRef.current = markers;

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.data?.type !== 'fpMarkerPress') return;
      const marker = markersRef.current.find((m) => m.id === event.data.id);
      if (marker) onMarkerPress(marker);
    }
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [onMarkerPress]);

  return (
    <View style={styles.container}>
      {React.createElement('iframe', {
        title: 'Incident Map',
        srcDoc: html,
        sandbox: 'allow-scripts',
        style: {
          border: 'none',
          width: '100%',
          height: '100%',
          display: 'block',
        },
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
});
