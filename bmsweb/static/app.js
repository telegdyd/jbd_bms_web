/*
 * The whole frontend. No build step and no framework: this serves one household on a home LAN,
 * and a toolchain would be the largest moving part of the project for no gain.
 *
 * Nothing here recomputes a figure. Distances, energy totals and splits all arrive finished from
 * the server, because the rules behind them are shared with the phone and a second copy in
 * JavaScript would drift from the first the moment either changed.
 */
'use strict';

const API = '/api/v1';
const SVG_NS = 'http://www.w3.org/2000/svg';

/* ---------------------------------------------------------------- utilities */

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

const ICONS = {
  download: '<path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 20h14"/>',
  trash: '<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/>',
  pencil: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  heart: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6"/><path d="M12 16.5v.5"/>',
  check: '<path d="M5 12l5 5 9-10"/>',
  warn: '<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4"/><path d="M12 17v.5"/>',
};

function icon(name) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = ICONS[name];
  return svg;
}

async function get(path) {
  const response = await fetch(API + path);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function patch(path, body) {
  const response = await fetch(API + path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function put(path, body) {
  const response = await fetch(API + path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    // A field out of range comes back as a list saying which; that is worth showing.
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = Array.isArray(body.detail)
        ? body.detail.map((d) => `${d.loc[d.loc.length - 1]}: ${d.msg}`).join('; ')
        : body.detail || detail;
    } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return response.json();
}

/* Uploads report their own failures in the body — "no timestamped track points in that file" is
 * the whole point of the message, and a bare status code would waste it. */
async function send(method, path, body) {
  const response = await fetch(API + path, { method, body });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return response.json();
}

const pad = (n) => String(n).padStart(2, '0');

/* The viewer's calendar day. The server groups by the day the rider saw, which for a household
 * that rides where it lives is the same one. */
function localIso(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function daysAgo(n) {
  const date = new Date();
  date.setDate(date.getDate() - n);
  return localIso(date);
}

const fmt = {
  duration(seconds) {
    seconds = Math.round(seconds || 0);
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h) return `${h}h ${pad(m)}m`;
    if (m) return `${m}m ${pad(s)}s`;
    return `${s}s`;
  },
  /* h:mm, for columns where every row should line up. */
  hm(seconds) {
    const minutes = Math.round((seconds || 0) / 60);
    return `${Math.floor(minutes / 60)}:${pad(minutes % 60)}`;
  },
  km: (v) => (v >= 10 ? v.toFixed(1) : v.toFixed(2)),
  number: (v, digits) => (v === null || v === undefined ? '—' : v.toFixed(digits)),
  /* Rounded before the sign is chosen, so a 40 cm drop reads as "0 m" rather than "-0 m". */
  metres(v) {
    if (v === null || v === undefined) return '—';
    const rounded = Math.round(v);
    return `${rounded > 0 ? '+' : ''}${rounded} m`;
  },
  /* Recordings carry their own UTC offset; rendering in the viewer's zone would move a ride to
   * another hour, or another day, depending on where it was read. */
  clock(ms, offsetMin) {
    const shifted = new Date(ms + (offsetMin ?? 0) * 60000);
    return `${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}`;
  },
  day(iso) {
    const date = new Date(iso + 'T00:00:00Z');
    if (iso === daysAgo(0)) return 'Today';
    if (iso === daysAgo(1)) return 'Yesterday';
    return date.toLocaleDateString(undefined, {
      weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC',
    });
  },
  shortDay(iso) {
    return new Date(iso + 'T00:00:00Z').toLocaleDateString(undefined, {
      weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC',
    });
  },
  energy(wh) {
    return wh >= 1000 ? [(wh / 1000).toFixed(2), 'kWh'] : [wh.toFixed(0), 'Wh'];
  },
  ago(ms) {
    const minutes = Math.round((Date.now() - ms) / 60000);
    if (minutes < 2) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 36) return `${hours} h ago`;
    return `${Math.round(hours / 24)} days ago`;
  },
};

/* Google encoded polyline, for the route thumbnails in the list and the overview map. */
function decodePolyline(encoded) {
  const points = [];
  let index = 0, lat = 0, lon = 0;
  while (index < encoded.length) {
    for (let axis = 0; axis < 2; axis++) {
      let shift = 0, result = 0, byte;
      do {
        byte = encoded.charCodeAt(index++) - 63;
        result |= (byte & 0x1f) << shift;
        shift += 5;
      } while (byte >= 0x20);
      const delta = result & 1 ? ~(result >> 1) : result >> 1;
      if (axis === 0) lat += delta; else lon += delta;
    }
    points.push([lat / 1e5, lon / 1e5]);
  }
  return points;
}

function thumbnail(encoded) {
  const blank = () => el('div', { class: 'thumb' });
  if (!encoded) return blank();
  const points = decodePolyline(encoded);
  if (points.length < 2) return blank();

  const lats = points.map((p) => p[0]);
  const lons = points.map((p) => p[1]);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  // Longitude degrees are shorter than latitude ones this far north; without the correction every
  // thumbnail comes out stretched sideways.
  const scale = Math.cos((((minLat + maxLat) / 2) * Math.PI) / 180);
  const width = Math.max((maxLon - minLon) * scale, 1e-9);
  const height = Math.max(maxLat - minLat, 1e-9);
  const span = Math.max(width * (46 / 72), height);

  const path = points
    .map(([la, lo], i) => {
      const x = 36 + ((lo - (minLon + maxLon) / 2) * scale * 34) / span;
      const y = 23 - ((la - (minLat + maxLat) / 2) * 34) / span;
      return `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(' ');

  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', 'thumb');
  svg.setAttribute('viewBox', '0 0 72 46');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = `<path d="${path}"/>`;
  return svg;
}

function tile(label, value, unit, delta) {
  return el('div', { class: 'tile' },
    el('div', { class: 'label' }, label),
    el('div', { class: 'value' }, value, unit ? el('small', {}, unit) : null),
    delta ? el('div', { class: 'delta' + (delta.better ? ' better' : '') }, delta.text) : null);
}

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

function speedColour(fraction) {
  // Blue through green to amber. Deliberately not a red-to-green ramp, which is the one pairing
  // that disappears for the most common kind of colour blindness.
  const stops = [[37, 99, 235], [34, 197, 94], [250, 180, 60]];
  const scaled = Math.max(0, Math.min(1, fraction)) * (stops.length - 1);
  const i = Math.min(Math.floor(scaled), stops.length - 2);
  const t = scaled - i;
  const mix = stops[i].map((c, k) => Math.round(c + (stops[i + 1][k] - c) * t));
  return `rgb(${mix.join(',')})`;
}

function nearest(times, ms) {
  let low = 0, high = times.length - 1;
  while (low < high) {
    const mid = (low + high) >> 1;
    if (times[mid] < ms) low = mid + 1; else high = mid;
  }
  if (low > 0 && Math.abs(times[low - 1] - ms) < Math.abs(times[low] - ms)) return low - 1;
  return low;
}

/* ------------------------------------------------------------------- shell */

/* The sidebar's counts and the line saying when the phone last delivered anything. Refreshed on
 * every page change, since a delete or a sync in the meantime changes both. */
async function refreshShell() {
  try {
    const [health, rides] = await Promise.all([
      get('/health'),
      get('/sessions?rides_only=true&limit=1'),
    ]);
    document.getElementById('count-rides').textContent = rides.total;
    document.getElementById('count-sessions').textContent = health.sessions;

    const sync = document.getElementById('sync');
    if (health.last_upload_ms) {
      const stale = Date.now() - health.last_upload_ms > 7 * 86400000;
      sync.className = 'sync' + (stale ? ' stale' : '');
      sync.replaceChildren(el('b', {}, 'Last upload'), fmt.ago(health.last_upload_ms));
      sync.hidden = false;
    }
  } catch { /* the page itself will say what is wrong */ }
}

/* ------------------------------------------------------------------- maps */

const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';

/*
 * A map built while its container has no usable size stays broken: Leaflet caches the viewport,
 * loads two tiles for a box a few pixels across, and every path — route included — renders as
 * "M0 0". Two things can cause it, and they need different cures.
 *
 * A container that gets its size late is caught by the ResizeObserver. A page opened in a
 * background tab is not: hidden documents run no rendering callbacks at all, so the map is built
 * blind, and when the tab is finally shown the element's size has not *changed*, so the observer
 * stays silent. Hence also listening for the tab becoming visible.
 *
 * `fit` is called once straight away, because Leaflet will not draw a layer on a map with no view,
 * and again the first time the map has a real size, because that first view was worked out for a
 * box of nothing.
 */
function baseMap(host, fit) {
  const map = L.map(host, { attributionControl: true, zoomControl: false, scrollWheelZoom: false });
  L.control.zoom({ position: 'bottomright' }).addTo(map);
  // These maps are most of a screen tall, so a scroll down the page that passes over one must keep
  // scrolling the page. The wheel zooms only once the map has been clicked into.
  map.on('click', () => map.scrollWheelZoom.enable());
  map.on('mouseout', () => map.scrollWheelZoom.disable());
  const tiles = L.tileLayer(TILE_URL, { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(map);
  fit(map);

  let fitted = false;
  const refresh = () => {
    if (!host.isConnected) {
      document.removeEventListener('visibilitychange', refresh);
      return;
    }
    if (document.hidden || !host.clientWidth || !host.clientHeight) return;
    map.invalidateSize();
    if (!fitted) {
      fit(map);
      fitted = true;
    }
  };
  new ResizeObserver(refresh).observe(host);
  document.addEventListener('visibilitychange', refresh);

  return { map, tiles };
}

/* Tiles need the internet on whatever computer is looking. The drawn track does not, and is
 * exactly what the phone falls back to. */
function tilesToggle(map, tiles) {
  return el('button', {
    type: 'button', 'aria-pressed': 'true',
    onclick: (event) => {
      const on = map.hasLayer(tiles);
      if (on) map.removeLayer(tiles); else { tiles.addTo(map); tiles.bringToBack(); }
      event.currentTarget.setAttribute('aria-pressed', String(!on));
    },
  }, 'Street map');
}

/* Every ride of a period on one map, with the latest picked out and a ring for how far the pack
 * reaches from home. */
function overviewMap(wrap, host, { onPick }) {
  const HOME = { lat: 47.4979, lon: 19.0402 };
  let bounds = null;
  let home = null;

  const { map, tiles } = baseMap(host, (m) => {
    if (bounds) m.fitBounds(bounds, { padding: [48, 48] });
    else if (home) m.setView([home.lat, home.lon], 12);
    else m.setView([HOME.lat, HOME.lon], 11);
  });

  const routeLayer = L.layerGroup().addTo(map);
  const ringLayer = L.layerGroup().addTo(map);
  const lines = new Map();
  let picked = null;
  let latest = null;

  function style(id) {
    const on = id === picked;
    return on
      ? { color: css('--pick'), weight: 4, opacity: 1 }
      : { color: css('--route'), weight: 2.5, opacity: 0.45 };
  }

  function pick(id) {
    picked = id ?? latest;
    for (const [key, line] of lines) line.setStyle(style(key));
    lines.get(picked)?.bringToFront();
    onPick(picked);
  }

  const ringButton = el('button', {
    type: 'button', class: 'ring', 'aria-pressed': 'true', hidden: true,
    onclick: (event) => {
      const on = map.hasLayer(ringLayer);
      if (on) map.removeLayer(ringLayer); else ringLayer.addTo(map);
      event.currentTarget.setAttribute('aria-pressed', String(!on));
      caption.hidden = on;
    },
  }, 'Range ring');
  const caption = el('div', { class: 'float caption', hidden: true });
  wrap.append(el('div', { class: 'float tools' }, tilesToggle(map, tiles), ringButton), caption);

  return {
    routes(sessions) {
      routeLayer.clearLayers();
      lines.clear();
      const all = [];
      for (const session of sessions) {
        if (!session.polyline) continue;
        const points = decodePolyline(session.polyline);
        if (points.length < 2) continue;
        all.push(...points);
        const line = L.polyline(points, style(session.id))
          .bindTooltip(
            `${session.title || defaultName(session)} · ${fmt.shortDay(session.local_date)} · ` +
              `${fmt.km(session.distance_km)} km`,
            { sticky: true, className: 'route-tip' })
          .on('mouseover', () => pick(session.id))
          .on('mouseout', () => pick(null))
          .on('click', () => { location.hash = `#/session/${session.id}`; })
          .addTo(routeLayer);
        lines.set(session.id, line);
      }
      latest = sessions.find((s) => lines.has(s.id))?.id ?? null;
      bounds = all.length ? L.latLngBounds(all) : null;
      if (bounds) map.fitBounds(bounds, { padding: [48, 48] });
      pick(null);
    },

    ring(pack) {
      ringLayer.clearLayers();
      if (!pack.home) return;
      home = pack.home;
      L.circleMarker([home.lat, home.lon], {
        radius: 6, color: css('--bg'), weight: 3, fillColor: css('--text'), fillOpacity: 1,
        interactive: false,
      }).addTo(ringLayer);
      if (!bounds) map.setView([home.lat, home.lon], 12);

      if (!pack.range_km) return;
      // Out and back: half the range is as far as the pack gets you from the door.
      L.circle([home.lat, home.lon], {
        radius: (pack.range_km / 2) * 1000,
        color: css('--good'), weight: 1.5, opacity: 0.6, dashArray: '6 6',
        fillColor: css('--good'), fillOpacity: 0.04, interactive: false,
      }).addTo(ringLayer);
      ringButton.hidden = false;
      caption.hidden = false;
      caption.replaceChildren(
        el('b', {}, `Range at ${pack.charge.soc} %`),
        `: about ${Math.round(pack.range_km / 2)} km out and back`);
    },

    pick,
  };
}

/* ---------------------------------------------------------------- ride lists */

/* What an untitled recording is called: by the time of day it started, in its own clock. */
function defaultName(session) {
  if ((session.charged_wh || 0) > (session.discharged_wh || 0)) return 'Charge';
  if (!session.is_ride) return 'Session';
  const hour = new Date(session.started_at_ms + (session.tz_offset_min ?? 0) * 60000).getUTCHours();
  return hour < 5 ? 'Night ride' : hour < 12 ? 'Morning ride' : hour < 18 ? 'Afternoon ride' : 'Evening ride';
}

function rideRow(session, { onEnter, onLeave } = {}) {
  const went = session.distance_km > 0.05;
  const average = went && session.moving_seconds
    ? session.distance_km / (session.moving_seconds / 3600) : null;
  const charging = (session.charged_wh || 0) > (session.discharged_wh || 0);

  return el('a', {
    class: 'ride-row', href: `#/session/${session.id}`, 'data-id': session.id,
    onmouseenter: onEnter, onmouseleave: onLeave,
  },
    thumbnail(session.polyline),
    el('div', { class: 'when' },
      el('span', {}, fmt.shortDay(session.local_date)),
      el('span', { class: 'num' }, fmt.clock(session.started_at_ms, session.tz_offset_min))),
    el('div', { class: 'title' },
      el('span', {}, session.title || defaultName(session)),
      session.kind === 'ekd01' ? el('span', { class: 'badge' }, 'display') : null,
      !session.has_location ? el('span', { class: 'badge' }, 'no gps') : null,
      session.avg_hr_bpm
        ? el('span', { class: 'badge hr', title: 'Average heart rate, from the watch' },
            icon('heart'), String(session.avg_hr_bpm))
        : null),
    el('span', { class: 'n big' }, went ? [fmt.km(session.distance_km), el('small', {}, ' km')] : '—'),
    el('span', { class: 'n' }, fmt.hm(session.moving_seconds || session.duration_ms / 1000)),
    el('span', { class: 'n' }, average ? average.toFixed(1) : '—'),
    el('span', { class: 'n' }, charging
      ? `+${session.charged_wh.toFixed(0)}`
      : session.discharged_wh ? session.discharged_wh.toFixed(0) : '—'),
    el('span', { class: 'n eff' }, session.wh_per_km ? session.wh_per_km.toFixed(1) : '—'));
}

function rideHeader() {
  return el('div', { class: 'ride-row header' },
    el('span'), el('span', {}, 'When'), el('span', {}, 'Ride'),
    el('span', { class: 'n' }, 'Distance'), el('span', { class: 'n' }, 'Moving'),
    el('span', { class: 'n' }, 'Avg km/h'), el('span', { class: 'n' }, 'Wh'),
    el('span', { class: 'n' }, 'Wh/km'));
}

async function listView(root, { ridesOnly, heading, blurb }) {
  const state = { kind: '', q: '' };

  const rows = el('div', { class: 'table' });
  const search = el('input', {
    type: 'search', placeholder: 'Search titles, notes, devices', 'aria-label': 'Search',
    oninput: debounce((event) => { state.q = event.target.value.trim(); load(); }, 250),
  });
  const kind = el('select', {
    'aria-label': 'Device',
    onchange: (event) => { state.kind = event.target.value; load(); },
  }, el('option', { value: '' }, 'All devices'),
     el('option', { value: 'bms' }, 'BMS'),
     el('option', { value: 'ekd01' }, 'Display'));

  root.append(el('div', { class: 'stack' },
    el('div', { class: 'head' },
      el('div', {}, el('h1', {}, heading), el('p', { class: 'sub' }, blurb)),
      el('div', { class: 'controls' }, search, kind)),
    rows));

  async function load() {
    const params = new URLSearchParams({ limit: '200' });
    if (ridesOnly) params.set('rides_only', 'true');
    if (state.kind) params.set('kind', state.kind);
    if (state.q) params.set('q', state.q);

    rows.replaceChildren(el('div', { class: 'empty' }, 'Loading…'));
    const body = await get('/sessions?' + params);

    if (!body.sessions.length) {
      rows.replaceChildren(el('div', { class: 'empty' },
        ridesOnly ? 'No rides yet. Recordings without GPS are under Sessions.' : 'Nothing recorded yet.'));
      return;
    }

    const children = [rideHeader()];
    let day = null;
    for (const session of body.sessions) {
      if (session.local_date !== day) {
        day = session.local_date;
        children.push(el('div', { class: 'day-row' }, fmt.day(day)));
      }
      children.push(rideRow(session));
    }
    rows.replaceChildren(...children);
  }

  await load();
}

/* --------------------------------------------------------------- dashboard */

const PERIODS = [
  { label: 'Week', heading: 'Last 7 days', days: 7 },
  { label: 'Month', heading: 'Last 30 days', days: 30 },
  { label: 'Year', heading: 'Last 12 months', days: 365 },
  { label: 'All', heading: 'All time', days: null },
];

async function dashboardView(root) {
  let period = PERIODS[1];

  const heading = el('h1', {}, period.heading);
  const periodButtons = el('div', { class: 'segmented', role: 'group', 'aria-label': 'Period' },
    PERIODS.map((p) => el('button', {
      type: 'button', 'aria-pressed': String(p === period),
      onclick: () => {
        period = p;
        heading.textContent = p.heading;
        for (const button of periodButtons.children) {
          button.setAttribute('aria-pressed', String(button.textContent === p.label));
        }
        loadPeriod();
      },
    }, p.label)));

  const mapHost = el('div', { class: 'map' });
  const mapWrap = el('section', { class: 'map-wrap overview', 'aria-label': 'Rides on a map' }, mapHost);
  const band = el('div', { class: 'band' });
  const recent = el('div', { class: 'table' });
  const weeks = el('div', { class: 'weeks' });
  const range = el('div', { class: 'pack-card' });
  const heat = el('div', { class: 'heat' });

  root.append(el('div', { class: 'stack' },
    el('div', { class: 'head' },
      el('div', {}, heading,
        el('p', { class: 'sub' }, 'Rides only. Bench and solar sessions are under Sessions.')),
      periodButtons),
    mapWrap,
    band,
    el('section', {},
      el('div', { class: 'section-head' },
        el('h2', {}, 'Rides ', el('span', { class: 'hint' }, 'hover one to find it on the map')),
        el('a', { href: '#/rides' }, 'All rides →')),
      recent),
    el('div', { class: 'split-row' },
      el('section', { class: 'card' },
        el('div', { class: 'section-head' }, el('h2', {}, 'Distance per week'),
          el('span', { class: 'hint' }, 'last 16 weeks · km')),
        weeks),
      el('section', { class: 'card' },
        el('div', { class: 'section-head' }, el('h2', {}, 'Range now')),
        range)),
    el('section', { class: 'card' },
      el('div', { class: 'section-head' }, el('h2', {}, 'Last 12 months')),
      heat)));

  const overview = overviewMap(mapWrap, mapHost, {
    onPick: (id) => {
      for (const row of recent.querySelectorAll('.ride-row[data-id]')) {
        row.classList.toggle('lit', Number(row.dataset.id) === id);
      }
    },
  });

  async function loadPeriod() {
    const since = period.days ? daysAgo(period.days - 1) : null;
    const params = new URLSearchParams({ rides_only: 'true', limit: '500' });
    if (since) params.set('since', since);

    const [current, previous, list] = await Promise.all([
      get('/stats' + (since ? `?since=${since}` : '')),
      period.days
        ? get(`/stats?since=${daysAgo(2 * period.days - 1)}&until=${daysAgo(period.days)}`)
        : Promise.resolve(null),
      get('/sessions?' + params),
    ]);

    band.replaceChildren(...totalsTiles(current.totals, previous && previous.totals));
    overview.routes(list.sessions);

    const shown = list.sessions.slice(0, 10);
    recent.replaceChildren(...(shown.length
      ? [rideHeader(), ...shown.map((s) => rideRow(s, {
          onEnter: () => overview.pick(s.id),
          onLeave: () => overview.pick(null),
        }))]
      : [el('div', { class: 'empty' }, 'No rides in this period.')]));
  }

  async function loadRange() {
    const pack = await get('/pack');
    overview.ring(pack);
    range.replaceChildren(...rangeCard(pack));
  }

  await Promise.all([loadPeriod(), loadRange(), drawWeeks(weeks), drawHeatmap(heat)]);
}

/* The period's totals, each against the same length of time just before it. Only efficiency is
 * marked better or worse: more kilometres is not better, it is just more. */
function totalsTiles(totals, before) {
  const change = (now, then) => {
    if (!before || !then) return null;
    const percent = ((now - then) / then) * 100;
    return { text: `${percent >= 0 ? '+' : '−'}${Math.abs(percent).toFixed(0)} % on the period before` };
  };
  const [energy, energyUnit] = fmt.energy(totals.discharged_wh);

  let efficiency = null;
  if (before && totals.wh_per_km && before.wh_per_km) {
    const diff = totals.wh_per_km - before.wh_per_km;
    efficiency = {
      text: Math.abs(diff) < 0.05 ? 'same as before'
        : `${Math.abs(diff).toFixed(1)} ${diff < 0 ? 'better' : 'worse'} than before`,
      better: diff < 0,
    };
  }

  return [
    tile('Rides', totals.sessions, '', before
      ? { text: `${totals.sessions - before.sessions >= 0 ? '+' : '−'}${Math.abs(totals.sessions - before.sessions)} on the period before` }
      : null),
    tile('Distance', fmt.km(totals.distance_km), 'km', change(totals.distance_km, before?.distance_km)),
    tile('Moving', fmt.hm(totals.moving_seconds), 'h', change(totals.moving_seconds, before?.moving_seconds)),
    tile('Energy', energy, energyUnit, change(totals.discharged_wh, before?.discharged_wh)),
    tile('Efficiency', fmt.number(totals.wh_per_km, 1), totals.wh_per_km ? 'Wh/km' : '', efficiency),
    tile('Top speed', fmt.number(totals.max_speed_kmh, 1), 'km/h',
      before?.max_speed_kmh ? { text: `before: ${before.max_speed_kmh.toFixed(1)}` } : null),
  ];
}

function rangeCard(pack) {
  if (!pack.charge) return [el('p', { class: 'note' }, 'Nothing recorded yet.')];
  if (!pack.capacity.usable_wh || !pack.wh_per_km) {
    return [
      el('div', { class: 'big' }, `${pack.charge.soc}`, el('small', {}, '% charge')),
      el('p', { class: 'note' },
        'A range needs one ride that used at least 30 % of the pack, to learn what a full ' +
        'charge holds.'),
    ];
  }
  return [
    el('div', { class: 'big' }, `≈ ${Math.round(pack.range_km)}`, el('small', {}, 'km')),
    el('div', {},
      el('div', { class: 'gauge' }, el('i', { style: `width:${pack.charge.soc}%` })),
      el('div', { class: 'meta-line', style: 'display:flex;justify-content:space-between;margin-top:6px' },
        el('span', {}, `${pack.charge.soc} % · ${fmt.shortDay(pack.charge.local_date)}`),
        el('span', {}, `${Math.round(pack.capacity.usable_wh)} Wh per charge`))),
    el('p', { class: 'note' },
      `At the last month's ${pack.wh_per_km.toFixed(1)} Wh/km, a full pack goes about ` +
      `${Math.round(pack.full_range_km)} km. Hills and cold cost more.`),
  ];
}

function niceMax(value) {
  const magnitude = 10 ** Math.floor(Math.log10(Math.max(value, 10)));
  for (const step of [1, 1.5, 2, 3, 4, 5, 6, 8, 10]) {
    if (step * magnitude >= value) return step * magnitude;
  }
  return 10 * magnitude;
}

async function drawWeeks(host) {
  const first = new Date();
  first.setHours(0, 0, 0, 0);
  first.setDate(first.getDate() - ((first.getDay() + 6) % 7) - 15 * 7);

  const { weeks } = await get('/stats?since=' + localIso(first));
  const byStart = new Map(weeks.map((w) => [w.week_start, w.distance_km]));
  const slots = Array.from({ length: 16 }, (_, i) => {
    const start = new Date(first);
    start.setDate(start.getDate() + i * 7);
    return { start, km: byStart.get(localIso(start)) ?? 0 };
  });

  const W = 572, H = 190, left = 36, top = 10, base = 160;
  const top_ = niceMax(Math.max(...slots.map((s) => s.km)));
  const slot = (W - left) / 16, bar = 20;
  const y = (km) => base - (km / top_) * (base - top);

  let markup = '';
  for (const tick of [0, top_ / 2, top_]) {
    markup += `<path class="grid" d="M${left} ${y(tick)}H${W}"/>` +
      `<text x="${left - 8}" y="${y(tick) + 4}" text-anchor="end">${tick}</text>`;
  }
  slots.forEach((s, i) => {
    const x = left + i * slot + (slot - bar) / 2;
    const h = Math.max(base - y(s.km), s.km > 0 ? 2 : 0);
    markup += `<rect class="${i === 15 ? 'now' : 'past'}" x="${x.toFixed(1)}" y="${(base - h).toFixed(1)}" ` +
      `width="${bar}" height="${h.toFixed(1)}" rx="3"><title>Week of ${localIso(s.start)}: ` +
      `${s.km.toFixed(1)} km</title></rect>`;
    // Named on the week that holds the 1st, which is where the month turns.
    const end = new Date(s.start);
    end.setDate(end.getDate() + 6);
    if (i === 0 || end.getMonth() !== s.start.getMonth()) {
      const month = end.toLocaleDateString(undefined, { month: 'short' });
      markup += `<text x="${(x + bar / 2).toFixed(1)}" y="${base + 18}" text-anchor="middle">${month}</text>`;
    }
  });

  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Distance per week over the last sixteen weeks');
  svg.innerHTML = markup;
  host.replaceChildren(svg);
}

async function drawHeatmap(host) {
  const since = new Date(Date.now() - 364 * 86400000);
  const { days } = await get('/stats?since=' + since.toISOString().slice(0, 10));
  const byDay = new Map(days.map((d) => [d.local_date, d]));
  const busiest = Math.max(1, ...days.map((d) => d.distance_km));

  // Start on the Monday before the window so the columns line up as weeks.
  const start = new Date(since);
  start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));

  const cells = [];
  for (let d = new Date(start); d <= new Date(); d.setUTCDate(d.getUTCDate() + 1)) {
    const iso = d.toISOString().slice(0, 10);
    const day = byDay.get(iso);
    const level = day ? Math.min(4, Math.ceil((day.distance_km / busiest) * 4)) : 0;
    cells.push(el('i', {
      'data-level': level,
      title: day ? `${iso} — ${fmt.km(day.distance_km)} km` : iso,
    }));
  }
  host.replaceChildren(...cells);
}

/* ------------------------------------------------------------ detail view */

const CHANNELS = [
  { field: 'watts', name: 'Power', unit: 'W', colour: '--power', digits: 0, fill: true, rider: true },
  /* Worked out by the server from the route, the heights and the pack's output; null without a
   * profile weight, and then not drawn. */
  { field: 'rider_w', name: 'Your power', unit: 'W', colour: '--rider', digits: 0, fill: true, note: 'estimated' },
  { field: 'speed_kmh', name: 'Speed', unit: 'km/h', colour: '--speed', digits: 1 },
  /* From the watch, in the recording itself; failing that, from an attached GPX. Asked for
   * unconditionally: the server answers with nulls when there is neither, and a chart of nulls is
   * not drawn. `series.sources` says which one it was. */
  { field: 'hr', name: 'Heart rate', unit: 'bpm', colour: '--hr', digits: 0, companion: true, rider: true },
  { field: 'cadence', name: 'Cadence', unit: 'rpm', colour: '--cadence', digits: 0, companion: true },
  { field: 'volts', name: 'Voltage', unit: 'V', colour: '--volts', digits: 2 },
  { field: 'soc', name: 'State of charge', unit: '%', colour: '--soc', digits: 0 },
  { field: 'alt_m', name: 'Altitude', unit: 'm', colour: '--alt', digits: 0, fill: true, rider: true },
  { field: 'delta_mv', name: 'Cell spread', unit: 'mV', colour: '--spread', digits: 0 },
];

/* One key ties every chart's cursor together — the strip on the map and the charts below it — so
 * a moment can be read across all of them at once, the same behaviour as the app's scrubber. */
const SYNC_KEY = 'session';

async function detailView(root, id) {
  const session = await get(`/sessions/${id}`);
  const isEkd01 = session.kind === 'ekd01';
  const onMap = Boolean(session.has_location);
  const fallbackTitle = session.title || defaultName(session);

  const title = el('h1', { contenteditable: 'true', spellcheck: 'false' }, fallbackTitle);
  const saved = el('span', { class: 'saving' });

  title.addEventListener('blur', async () => {
    const value = title.textContent.trim();
    if (value === (session.title || defaultName(session))) return;
    saved.textContent = 'saving…';
    await patch(`/sessions/${id}`, { title: value });
    session.title = value;
    saved.textContent = 'saved';
    setTimeout(() => { saved.textContent = ''; }, 1500);
  });
  title.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); title.blur(); }
  });

  async function remove() {
    const what = session.is_ride ? 'ride' : 'session';
    if (!window.confirm(`Delete this ${what}? The original CSV goes to the trash directory.`)) return;
    saved.textContent = 'deleting…';
    try {
      await send('DELETE', `/sessions/${id}`);
      location.hash = session.is_ride ? '#/rides' : '#/sessions';
    } catch (error) {
      saved.textContent = String(error.message || error);
    }
  }

  const back = session.is_ride ? ['#/rides', 'Rides'] : ['#/sessions', 'Sessions'];
  const page = el('div', { class: 'stack' });
  root.append(page);

  page.append(el('div', { style: 'display:flex;flex-direction:column;gap:6px' },
    el('nav', { class: 'crumbs', 'aria-label': 'Breadcrumb' },
      el('a', { href: back[0] }, back[1]), ' / ', fmt.day(session.local_date)),
    el('div', { class: 'detail-head' },
      el('div', { style: 'display:flex;align-items:center;gap:6px' }, title,
        el('button', {
          class: 'pill', type: 'button', 'aria-label': 'Rename', style: 'border-color:transparent',
          onclick: () => {
            title.focus();
            getSelection().selectAllChildren(title);
          },
        }, icon('pencil'))),
      el('div', { class: 'controls' },
        el('a', { class: 'pill', href: `${API}/sessions/${id}/raw.csv` }, icon('download'), 'CSV'),
        el('button', { class: 'pill danger', type: 'button', onclick: remove }, icon('trash'), 'Delete'))),
    el('div', { class: 'meta-line' },
      [fmt.shortDay(session.local_date),
       `${fmt.clock(session.started_at_ms, session.tz_offset_min)} → ` +
         fmt.clock(session.ended_at_ms, session.tz_offset_min),
       session.device_label,
       `${session.sample_count.toLocaleString()} samples`].filter(Boolean).join(' · '),
      ' ', saved)));

  // The map, with the ride's figures and the rider's traces floating over it; or, for a recording
  // that went nowhere, the figures as tiles.
  let mapParts = null;
  if (onMap) {
    mapParts = {
      host: el('div', { class: 'map' }),
      panel: figuresPanel(session),
      keys: el('div', { class: 'row' }),
      readout: el('div', { class: 'readout' }),
      strip: el('div', {}),
    };
    mapParts.profile = el('div', { class: 'float profile' },
      el('div', { class: 'row' }, mapParts.keys, mapParts.readout), mapParts.strip);
    mapParts.wrap = el('section', { class: 'map-wrap ride', 'aria-label': 'Route' },
      mapParts.host, mapParts.panel, mapParts.profile);
    page.append(mapParts.wrap);
  } else {
    page.append(summaryTiles(session, isEkd01));
  }

  if (session.gap_count) {
    page.append(el('div', { class: 'notice' }, icon('info'),
      `${session.gap_count} dropout${session.gap_count === 1 ? '' : 's'} totalling ` +
      `${fmt.duration(session.gap_ms / 1000)}. Their watt-hours are left out of the totals, not guessed.`));
  }

  // The rider's side: filled in once the effort figures arrive, and left out entirely for a
  // recording that has neither a route nor a heart rate to say anything about.
  const effortHost = el('section', { class: 'card', hidden: true });
  page.append(effortHost);

  const chartHost = el('div', {});
  page.append(el('section', { class: 'card' },
    el('div', { class: 'section-head' },
      el('h2', {}, onMap ? 'The pack through the ride' : 'Charts'),
      el('span', { class: 'hint' }, onMap ? 'same cursor as the strip on the map' : '')),
    chartHost));

  const splitHost = el('div', {});
  const companionHost = el('div', {});
  const hrSummary = el('span', { class: 'hint' });
  const notes = el('textarea', { class: 'notes', id: 'notes', placeholder: 'Notes about this ride…' });
  notes.value = session.notes || '';
  const notesStatus = el('span', { class: 'saving' }, 'Saved when you click away.');
  notes.addEventListener('blur', async () => {
    if (notes.value === (session.notes || '')) return;
    notesStatus.textContent = 'saving…';
    await patch(`/sessions/${id}`, { notes: notes.value });
    session.notes = notes.value;
    notesStatus.textContent = 'Saved.';
  });

  const side = el('div', { class: 'stack' },
    el('section', { class: 'card' },
      el('div', { class: 'section-head' },
        el('h2', { style: 'display:flex;align-items:center;gap:8px' },
          el('span', { style: `color:${css('--hr')};display:flex` }, icon('heart')), 'Heart rate'),
        hrSummary),
      companionHost),
    el('section', { class: 'card', style: 'display:flex;flex-direction:column;gap:10px' },
      el('label', { for: 'notes', style: 'font-weight:600' }, 'Notes'), notes, notesStatus));

  page.append(onMap
    ? el('div', { class: 'two-col' },
        el('section', { class: 'card' },
          el('div', { class: 'section-head' }, el('h2', {}, 'Splits'),
            el('span', { class: 'hint' }, 'per kilometre')),
          splitHost),
        side)
    : side);

  const wanted = CHANNELS.filter(
    (c) => (isEkd01 ? ['speed_kmh', 'soc'].includes(c.field) : true) || c.companion);
  const [track, series, splitBody, companions, effortBody] = await Promise.all([
    onMap ? get(`/sessions/${id}/track`) : Promise.resolve(null),
    get(`/sessions/${id}/series?fields=${wanted.map((c) => c.field).join(',')}&points=3000`),
    onMap ? get(`/sessions/${id}/splits`) : Promise.resolve(null),
    get(`/sessions/${id}/companions`),
    get(`/sessions/${id}/effort`),
  ]);
  drawEffort(effortHost, effortBody);

  const marker = onMap ? drawRideMap(mapParts, track) : null;
  const onStrip = onMap ? drawProfile(mapParts, series, wanted.filter((c) => c.rider), session, marker) : [];
  drawCharts(chartHost, series, wanted.filter((c) => !onStrip.includes(c)), marker);
  if (splitBody) drawSplits(splitHost, splitBody, session.max_speed_kmh);
  const fromWatch = session.avg_hr_bpm !== null && session.avg_hr_bpm !== undefined;
  drawCompanions(companionHost, id, companions.companions, fromWatch);

  // The watch's own figures when it measured; they cover exactly the recording, with no clock to
  // line up. Otherwise whichever attached file covers the ride best.
  const best = fromWatch
    ? { hr_avg: session.avg_hr_bpm, hr_max: session.max_hr_bpm }
    : bestHeartRate(companions.companions);
  if (best) {
    hrSummary.textContent = `avg ${Math.round(best.hr_avg)} · max ${best.hr_max} bpm`;
    if (!onMap) page.querySelector('.tiles')?.append(
      tile('Avg HR', Math.round(best.hr_avg), 'bpm'), tile('Max HR', best.hr_max, 'bpm'));
  }
}

/* The ride's own figures, as they sit over its map. The headline and six beside it are what gets
 * looked at; everything else is one click away rather than crowding the route. */
function figuresPanel(session) {
  const went = session.distance_km > 0.05;
  const hero = [];
  if (went && session.moving_seconds) {
    hero.push(tile('Moving', fmt.duration(session.moving_seconds)));
    hero.push(tile('Average',
      (session.distance_km / (session.moving_seconds / 3600)).toFixed(1), 'km/h'));
  }
  if (session.max_speed_kmh) hero.push(tile('Top speed', session.max_speed_kmh.toFixed(1), 'km/h'));
  hero.push(tile('Energy', session.discharged_wh.toFixed(0), 'Wh'));
  if (session.wh_per_km) {
    const efficiency = tile('Efficiency', session.wh_per_km.toFixed(1), 'Wh/km');
    efficiency.querySelector('.value').style.color = css('--power');
    hero.push(efficiency);
  }
  hero.push(tile('Charge', `${session.soc_start}→${session.soc_end}`, '%'));

  const facts = [
    ['Elapsed', fmt.duration(session.duration_ms / 1000)],
    session.charged_wh > 0.05 ? ['Charged', `${session.charged_wh.toFixed(1)} Wh`] : null,
    ['Peak output', `${session.peak_discharge_w.toFixed(0)} W`],
    ['Voltage', `${session.min_volts.toFixed(2)} – ${session.max_volts.toFixed(2)} V`],
    session.max_delta_mv !== null ? ['Worst cell spread', `${session.max_delta_mv} mV`] : null,
    session.min_temp_c !== null
      ? ['Temperature', `${session.min_temp_c.toFixed(1)} – ${session.max_temp_c.toFixed(1)} °C`] : null,
    session.avg_hr_bpm !== null
      ? ['Heart rate', `avg ${session.avg_hr_bpm} · max ${session.max_hr_bpm} bpm`] : null,
    ['Distance, exact', `${session.distance_km.toFixed(3)} km`],
  ].filter(Boolean);

  return el('div', { class: 'float figures-panel' },
    el('div', { class: 'headline' },
      went ? fmt.km(session.distance_km) : fmt.duration(session.duration_ms / 1000),
      went ? el('small', {}, 'km') : null),
    el('div', { class: 'grid' }, hero),
    el('details', {},
      el('summary', {}, 'All figures'),
      el('dl', { class: 'facts' },
        facts.map(([k, v]) => el('div', {}, el('dt', {}, k), el('dd', {}, v))))));
}

function summaryTiles(session, isEkd01) {
  const tiles = [
    tile('Duration', fmt.duration(session.duration_ms / 1000)),
    tile('SOC', `${session.soc_start}% → ${session.soc_end}%`),
  ];

  if (session.distance_km > 0.05) {
    tiles.push(tile('Distance', fmt.km(session.distance_km), 'km'));
    if (session.moving_seconds) {
      tiles.push(tile('Moving', fmt.duration(session.moving_seconds)));
      tiles.push(tile('Average',
        (session.distance_km / (session.moving_seconds / 3600)).toFixed(1), 'km/h'));
    }
    if (session.max_speed_kmh) tiles.push(tile('Top speed', session.max_speed_kmh.toFixed(1), 'km/h'));
  }

  if (!isEkd01) {
    tiles.push(tile('Discharged', session.discharged_wh.toFixed(1), 'Wh'));
    if (session.charged_wh > 0.05) tiles.push(tile('Charged', session.charged_wh.toFixed(1), 'Wh'));
    if (session.wh_per_km) tiles.push(tile('Efficiency', session.wh_per_km.toFixed(1), 'Wh/km'));
    tiles.push(tile('Peak out', session.peak_discharge_w.toFixed(0), 'W'));
    tiles.push(tile('Voltage', `${session.min_volts.toFixed(2)}–${session.max_volts.toFixed(2)}`, 'V'));
    if (session.max_delta_mv !== null) tiles.push(tile('Worst spread', session.max_delta_mv, 'mV'));
    if (session.min_temp_c !== null) {
      tiles.push(tile('Temperature',
        `${session.min_temp_c.toFixed(1)}–${session.max_temp_c.toFixed(1)}`, '°C'));
    }
  }

  return el('div', { class: 'tiles' }, tiles);
}

/* --------------------------------------------------------------------- effort */

const ALTITUDE_SOURCES = {
  barometer: 'Heights from the watch\'s barometer, levelled against the elevation map.',
  terrain: 'Heights from the elevation map under the route (SRTM, ~30 m).',
  gps: 'Heights from GPS altitude — the elevation map was not available, so treat this loosely.',
};

const ZONE_NAMES = ['Easy', 'Endurance', 'Tempo', 'Threshold', 'Maximum'];

function pct(fraction) { return `${Math.round(fraction * 100)}`; }

/* The rider's side of the ride, from /effort. Every part is optional: a ride with no route still
 * has zones, a profile with no year of birth still has a share of the work. What is missing says
 * so, with the one thing that would fill it in. */
function drawEffort(host, body) {
  const { rider, zones, calories } = body;
  const needsProfile = body.profile_missing.length > 0 && (
    body.rider_unavailable === 'profile' || body.zones_unavailable === 'profile' ||
    body.calories_unavailable === 'profile');
  if (!rider && !zones && !calories && !needsProfile) return;

  const parts = [];

  if (rider) {
    const tiles = [
      rangeTile('Your work', rider.rider_wh.toFixed(0), 'Wh',
        `${rider.rider_wh_low.toFixed(0)}–${rider.rider_wh_high.toFixed(0)} Wh`),
      rangeTile('Your share', pct(rider.share), '%',
        `${pct(rider.share_low)}–${pct(rider.share_high)} %`),
    ];
    if (rider.average_w !== null) {
      tiles.push(rangeTile('Your average', rider.average_w.toFixed(0), 'W', 'while moving'));
    }
    tiles.push(rangeTile('Motor', rider.motor_wh.toFixed(0), 'Wh', 'into the wheel'));
    if (calories && calories.kcal !== null) {
      tiles.push(rangeTile('Calories', calories.kcal, 'kcal', 'from heart rate'));
    }

    const share = Math.max(0, Math.min(1, rider.share || 0));
    parts.push(el('div', { class: 'tiles' }, tiles));
    parts.push(el('div', { class: 'share' },
      el('div', { class: 'bar-pair', role: 'img', 'aria-label': `You ${pct(share)} %, motor ${pct(1 - share)} %` },
        el('i', { style: `width:${share * 100}%;background:${css('--rider')}` }),
        el('i', { style: `width:${(1 - share) * 100}%;background:${css('--power')}` })),
      el('div', { class: 'keys' },
        el('span', {}, el('i', { class: 'key-dot', style: `background:${css('--rider')}` }), 'You ',
          el('b', {}, `${rider.rider_wh.toFixed(1)} Wh`)),
        el('span', {}, el('i', { class: 'key-dot', style: `background:${css('--power')}` }), 'Motor ',
          el('b', {}, `${rider.motor_wh.toFixed(1)} Wh`)))));
  } else if (calories && calories.kcal !== null) {
    parts.push(el('div', { class: 'tiles' }, rangeTile('Calories', calories.kcal, 'kcal', 'from heart rate')));
  }

  if (zones) parts.push(zoneBar(zones));

  const fine = [];
  if (rider) {
    fine.push(`${ALTITUDE_SOURCES[body.altitude_source] || ''} Worked out from ${rider.mass_kg.toFixed(0)} kg ` +
      'of rider and bike, the pack\'s output and the physics of rolling, air and climbing; the range ' +
      'covers plausible tyres, drag and drivetrain losses. Where the slope alone carries you, you ' +
      'count as coasting — so effort that is not pedalling, like holding on down a trail, is not in it.');
  }
  if (calories && calories.kcal !== null) {
    fine.push(`Calories from heart rate (Keytel)${calories.sex_given ? '' : ', averaged over both sexes'}; ` +
      'expect ±20–30 %.');
  }
  if (zones) {
    const from = { set: 'as you set it', recorded: 'the highest you have recorded', age: 'estimated from your age' }[zones.max_hr_source];
    fine.push(`Zones against a maximum of ${zones.max_hr} bpm, ${from}.`);
  }

  if (needsProfile) {
    const wants = body.profile_missing.map((f) => ({ weight_kg: 'weight', birth_year: 'year of birth' }[f]));
    parts.push(el('div', { class: 'notice' }, icon('info'),
      el('span', {}, `Add your ${wants.join(' and ')} `, el('a', { href: '#/profile' }, 'on your profile'),
        body.rider_unavailable === 'profile'
          ? ' to see how much of this ride was you, and what it cost.'
          : ' for heart rate zones and calories.')));
  }
  if (fine.length) {
    parts.push(el('p', { class: 'fine' }, fine.join(' '), ' ', el('a', { href: '#/profile' }, 'Your profile →')));
  }

  host.replaceChildren(
    el('div', { class: 'section-head' }, el('h2', {}, 'Your effort'),
      el('span', { class: 'hint' }, rider ? 'estimated' : '')),
    el('div', { class: 'effort' }, parts));
  host.hidden = false;
}

function rangeTile(label, value, unit, range) {
  const node = tile(label, value, unit);
  if (range) node.append(el('div', { class: 'range' }, range));
  return node;
}

function zoneBar(zones) {
  const total = zones.seconds.reduce((a, b) => a + b, 0) || 1;
  const edges = [null, ...zones.bounds, null];
  const colour = (i) => css(`--z${i + 1}`);
  return el('div', { class: 'zones' },
    el('div', { class: 'head' }, el('b', {}, 'Heart rate zones'), el('span', {}, `max ${zones.max_hr} bpm`)),
    el('div', { class: 'bar-pair', role: 'img', 'aria-label': 'Time in heart rate zones' },
      zones.seconds.map((s, i) => s
        ? el('i', { style: `width:${(s / total) * 100}%;background:${colour(i)}`, title: ZONE_NAMES[i] })
        : null)),
    el('div', { class: 'keys' },
      zones.seconds.map((s, i) => el('span', {
        title: edges[i] === null ? `below ${edges[i + 1]} bpm`
          : edges[i + 1] === null ? `${edges[i]} bpm and above` : `${edges[i]}–${edges[i + 1] - 1} bpm`,
      },
      el('i', { class: 'key-dot', style: `background:${colour(i)}` }),
      `Z${i + 1} ${ZONE_NAMES[i]} `, el('b', {}, fmt.duration(s))))));
}

/* ---------------------------------------------------------------- profile */

async function profileView(root) {
  const body = await get('/profile');
  const status = el('span', { class: 'saving' });

  const number = (name, attrs) => el('input', {
    type: 'number', name, value: body[name] ?? '', ...attrs,
  });
  const choice = (name, options, fallback) => {
    const select = el('select', { name },
      options.map(([value, label]) => el('option', { value }, label)));
    select.value = body[name] ?? fallback;
    return select;
  };

  const fields = {
    weight_kg: number('weight_kg', { min: 25, max: 250, step: '0.1', placeholder: 'kg' }),
    birth_year: number('birth_year', { min: 1900, max: new Date().getFullYear(), step: '1', placeholder: 'e.g. 1990' }),
    sex: choice('sex', [['', 'Not given'], ['male', 'Male'], ['female', 'Female']], ''),
    bike_kg: number('bike_kg', { min: 5, max: 100, step: '0.1', placeholder: String(body.defaults.bike_kg) }),
    tyres: choice('tyres', [['road', 'Road — smooth and hard'], ['mixed', 'Mixed — some gravel and trail'], ['offroad', 'Off-road — knobbly, mostly trail']], body.defaults.tyres),
    max_hr: number('max_hr', { min: 100, max: 230, step: '1', placeholder: body.max_hr_used ? `${body.max_hr_used} (estimated)` : 'bpm' }),
  };

  const field = (label, input, help) => el('label', {}, label, input, help ? el('small', {}, help) : null);

  async function save(event) {
    event.preventDefault();
    const value = (name) => fields[name].value.trim();
    const numeric = (name) => (value(name) === '' ? null : Number(value(name)));
    status.textContent = 'saving…';
    try {
      await put('/profile', {
        weight_kg: numeric('weight_kg'),
        birth_year: numeric('birth_year'),
        sex: value('sex') || null,
        bike_kg: numeric('bike_kg'),
        tyres: value('tyres') || null,
        max_hr: numeric('max_hr'),
      });
      route();
    } catch (error) {
      status.textContent = String(error.message || error);
    }
  }

  const derived = [];
  if (body.max_hr_used) {
    const why = {
      set: 'as you set it',
      recorded: 'the highest your rides have recorded, which is above the estimate for your age',
      age: `estimated from your age (208 − 0.7 × ${body.age}); it can be ±10 bpm off for any one person`,
    }[body.max_hr_source];
    derived.push(el('p', { style: 'margin:0' }, 'Maximum heart rate in use: ',
      el('b', {}, `${body.max_hr_used} bpm`), `, ${why}.`,
      body.max_hr_recorded ? ` Highest recorded so far: ${body.max_hr_recorded} bpm.` : ''));
    const edges = [null, ...body.zone_bounds, null];
    derived.push(el('table', {},
      el('thead', {}, el('tr', {}, el('th', {}, 'Zone'), el('th', {}, 'bpm'))),
      el('tbody', {}, ZONE_NAMES.map((name, i) => el('tr', {},
        el('td', {}, el('i', { class: 'key-dot', style: `background:${css(`--z${i + 1}`)}` }), `Z${i + 1} ${name}`),
        el('td', {}, edges[i] === null ? `< ${edges[i + 1]}`
          : edges[i + 1] === null ? `${edges[i]} +` : `${edges[i]}–${edges[i + 1] - 1}`))))));
  } else {
    derived.push(el('p', { class: 'sub', style: 'margin:0' },
      'Add your year of birth, or a maximum heart rate you know, for heart rate zones.'));
  }

  root.append(el('div', { class: 'stack' },
    el('div', {},
      el('h1', {}, 'You'),
      el('p', { class: 'sub' }, 'What the rider-side figures need. Everything is optional; each figure ' +
        'says what it is missing. Changes apply to every ride at once.')),
    el('form', { class: 'card stack', onsubmit: save },
      el('div', { class: 'section-head' }, el('h2', {}, 'Rider')),
      el('div', { class: 'profile-form' },
        field('Weight (kg)', fields.weight_kg, 'For your share of the work, and calories.'),
        field('Year of birth', fields.birth_year, 'For the heart rate estimate and calories.'),
        field('Sex', fields.sex, 'Only the calorie formula uses it; without it, both are averaged.'),
        field('Maximum heart rate', fields.max_hr, 'Leave empty unless you have measured it.')),
      el('div', { class: 'section-head' }, el('h2', {}, 'Bike')),
      el('div', { class: 'profile-form' },
        field('Bike weight (kg)', fields.bike_kg, `With the battery. Empty uses ${body.defaults.bike_kg} kg.`),
        field('Tyres', fields.tyres, 'Sets the rolling resistance the estimate starts from.')),
      el('div', { class: 'controls' },
        el('button', { class: 'pill', type: 'submit' }, 'Save'), status)),
    el('section', { class: 'card derived' },
      el('div', { class: 'section-head' }, el('h2', {}, 'Heart rate zones')),
      derived)));
}

/* ------------------------------------------------------------------- ride map */

const COLOUR_BY = {
  speed: { label: 'Speed', field: 'speed_kmh', unit: 'km/h' },
  power: { label: 'Power', field: 'watts', unit: 'W' },
  /* Scaled from the ride's lowest reading rather than from zero: a heart never goes near zero, and
   * against a zero floor 100 and 145 bpm are two shades of the same green. Offered only when the
   * watch measured something. */
  hr: { label: 'Heart rate', field: 'hr', unit: 'bpm', fromLowest: true, optional: true },
};

function drawRideMap(parts, track) {
  const points = track.points;
  const latlngs = points.map((p) => [p.lat, p.lon]);

  /* The route is fitted into the part of the map the floating panels leave clear. On a narrow
   * screen they are not floating at all, and the whole map is clear. */
  const fit = (map) => {
    if (!latlngs.length) { map.setView([47.4979, 19.0402], 11); return; }
    const floating = getComputedStyle(parts.panel).position === 'absolute';
    map.fitBounds(latlngs, floating
      ? {
          paddingTopLeft: [parts.panel.offsetWidth + 48, 72],
          paddingBottomRight: [48, parts.profile.offsetHeight + 40],
        }
      : { padding: [24, 24] });
  };
  const { map, tiles } = baseMap(parts.host, fit);
  if (!points.length) return null;

  // Coloured per segment. This is the cleaned-up track, not every fix: one polyline per fix would
  // be ten thousand layers on a long ride, and the map would stop panning.
  const layer = L.layerGroup().addTo(map);
  const legend = el('div', { class: 'float legend' });
  const buttons = {};

  const measured = (field) => points.map((p) => p[field]).filter((v) => v !== null && v !== undefined);

  function colourBy(mode) {
    const { field, unit, fromLowest } = COLOUR_BY[mode];
    const values = measured(field);
    const top = values.length ? Math.max(...values) : 0;
    const bottom = fromLowest && values.length ? Math.min(...values) : 0;
    const span = Math.max(top - bottom, 1);
    layer.clearLayers();

    if (top > 0) {
      for (let i = 0; i < latlngs.length - 1; i++) {
        const value = points[i][field];
        // A stretch the watch missed is left grey rather than painted as its calmest.
        const missing = fromLowest && (value === null || value === undefined);
        L.polyline([latlngs[i], latlngs[i + 1]], {
          color: missing ? css('--muted') : speedColour(((value ?? 0) - bottom) / span),
          weight: 5,
          opacity: 0.95,
        }).addTo(layer);
      }
      legend.replaceChildren(el('span', {}, bottom.toFixed(0)), el('i'),
        el('span', {}, `${top.toFixed(0)} ${unit}`));
      legend.hidden = false;
    } else {
      L.polyline(latlngs, { color: css('--accent'), weight: 5 }).addTo(layer);
      legend.hidden = true;
    }
    for (const [key, button] of Object.entries(buttons)) {
      button.setAttribute('aria-pressed', String(key === mode));
    }
  }

  const tools = el('div', { class: 'float tools right' });
  for (const [key, option] of Object.entries(COLOUR_BY)) {
    if (option.optional && !measured(option.field).length) continue;
    buttons[key] = el('button', { type: 'button', onclick: () => colourBy(key) }, option.label);
    tools.append(buttons[key]);
  }
  tools.append(tilesToggle(map, tiles));
  parts.wrap.append(tools, legend);
  colourBy('speed');

  L.circleMarker(latlngs[0], { radius: 7, color: '#fff', weight: 2.5, fillColor: '#22c55e', fillOpacity: 1 }).addTo(map);
  L.circleMarker(latlngs[latlngs.length - 1], { radius: 7, color: '#fff', weight: 2.5, fillColor: '#ef4444', fillOpacity: 1 }).addTo(map);

  const cursor = L.circleMarker(latlngs[0], {
    radius: 7, color: '#fff', weight: 2.5, fillColor: css('--accent'), fillOpacity: 1,
  });

  return {
    times: points.map((p) => p.t_ms),
    latlngs,
    show(ms) {
      if (ms === null) { map.removeLayer(cursor); return; }
      const index = nearest(this.times, ms);
      cursor.setLatLng(this.latlngs[index]);
      if (!map.hasLayer(cursor)) cursor.addTo(map);
    },
  };
}

/* ---------------------------------------------------------------- charts */

function presentIn(series, channels) {
  return channels.filter((c) => (series.fields[c.field] || []).some((v) => v !== null));
}

/* Altitude, power and heart rate as one strip along the foot of the map: the rider's side of the
 * ride, read against the route it happened on. Each has its own scale, so none flattens another.
 * Returns the channels it drew, which the charts below then leave out. */
function drawProfile(parts, series, channels, session, marker) {
  // Altitude first: it is the backdrop, and its fill is opaque, so drawn after the others it
  // would paint over every trace running below the height of the ground.
  const present = presentIn(series, channels)
    .sort((a, b) => (b.field === 'alt_m') - (a.field === 'alt_m'));
  if (!present.length) {
    parts.profile.hidden = true;
    return [];
  }

  parts.keys.replaceChildren(...present.map((c) =>
    el('span', { class: 'key' }, el('i', { style: `background:${css(c.colour)}` }), c.name)));
  const time = el('span', { style: `color:${css('--muted')}` });
  const readings = present.map((c) => el('span', { style: `color:${css(c.colour)}` }));
  parts.readout.replaceChildren(time, ...readings);

  const seconds = series.t.map((ms) => ms / 1000);
  const height = 96;
  const chart = new uPlot({
    width: parts.strip.clientWidth || 900,
    height,
    cursor: { sync: { key: SYNC_KEY, setSeries: false }, y: false, points: { show: false } },
    legend: { show: false },
    scales: Object.fromEntries([['x', { time: true }], ...present.map((c) => [c.field, {}])]),
    axes: [
      {
        stroke: css('--muted'), size: 22, grid: { show: false }, ticks: { show: false },
        font: `11px ${css('--mono')}`,
      },
      // uPlot adds a y axis of its own when given only an x one; this one is simply not shown.
      { scale: present[0].field, show: false },
    ],
    series: [
      {},
      ...present.map((c) => ({
        scale: c.field,
        stroke: c.field === 'alt_m' ? css('--line-2') : css(c.colour),
        fill: c.field === 'alt_m' ? css('--surface-2') : undefined,
        width: c.field === 'alt_m' ? 1 : 1.4,
        spanGaps: false,
        points: { show: false },
      })),
    ],
    hooks: {
      setCursor: [(u) => {
        const index = u.cursor.idx;
        if (index === null || index === undefined) {
          time.textContent = '';
          readings.forEach((r) => { r.textContent = ''; });
          if (marker) marker.show(null);
          return;
        }
        const ms = u.data[0][index] * 1000;
        time.textContent = fmt.clock(ms, session.tz_offset_min);
        present.forEach((c, k) => {
          const value = u.data[k + 1][index];
          readings[k].textContent = value === null || value === undefined
            ? '—' : `${value.toFixed(c.digits)} ${c.unit}`;
        });
        if (marker) marker.show(ms);
      }],
    },
  }, [seconds, ...present.map((c) => series.fields[c.field])], parts.strip);

  new ResizeObserver(() => chart.setSize({ width: parts.strip.clientWidth, height }))
    .observe(parts.strip);
  return present;
}

function sourceNote(series, channel) {
  const source = (series.sources || {})[channel.field] || 'companion';
  return source === 'recording' ? 'from the watch' : 'from the attached GPX';
}

function drawCharts(host, series, channels, marker) {
  const seconds = series.t.map((ms) => ms / 1000);
  const present = presentIn(series, channels);

  if (!present.length) {
    host.append(el('div', { class: 'empty' }, 'Nothing to chart in this recording.'));
    return;
  }

  const charts = [];
  // One time axis, under the last chart: repeated under each it would take half of every chart's
  // height to say the same thing seven times.
  const heightOf = (index) => (index === present.length - 1 ? 128 : 84);

  for (const [index, channel] of present.entries()) {
    const values = series.fields[channel.field];
    const reading = el('span', { class: 'reading', style: `color:${css(channel.colour)}` });
    const box = el('div', { class: 'chart' },
      el('div', { class: 'head' },
        el('span', { class: 'name' }, channel.name,
          channel.companion ? el('small', {}, sourceNote(series, channel)) : null,
          channel.note ? el('small', {}, channel.note) : null),
        reading));
    host.append(box);

    const colour = css(channel.colour);
    const chart = new uPlot({
      width: host.clientWidth || 800,
      height: heightOf(index),
      cursor: {
        sync: { key: SYNC_KEY, setSeries: false },
        y: false,
        points: { show: true },
      },
      legend: { show: false },
      scales: { x: { time: true } },
      axes: [
        index === present.length - 1
          ? { stroke: css('--muted'), grid: { stroke: css('--line'), width: 1 }, ticks: { stroke: css('--line') }, font: `11px ${css('--mono')}` }
          : { show: false },
        { stroke: css('--muted'), size: 52, grid: { stroke: css('--line'), width: 1 }, ticks: { stroke: css('--line') }, font: `11px ${css('--mono')}` },
      ],
      series: [
        {},
        {
          label: channel.name,
          stroke: colour,
          width: 1.6,
          fill: channel.fill ? colour + '22' : undefined,
          // Dropouts must read as breaks, not as a straight line drawn across missing time.
          spanGaps: false,
          points: { show: false },
        },
      ],
      hooks: {
        setCursor: [(u) => {
          const index = u.cursor.idx;
          if (index === null || index === undefined) {
            reading.textContent = '';
            if (marker) marker.show(null);
            return;
          }
          const value = u.data[1][index];
          reading.textContent = value === null || value === undefined
            ? '—'
            : `${value.toFixed(channel.digits)} ${channel.unit}`;
          if (marker) marker.show(u.data[0][index] * 1000);
        }],
      },
    }, [seconds, values], box);

    charts.push(chart);
  }

  const resize = new ResizeObserver(() => {
    charts.forEach((chart, index) => chart.setSize({ width: host.clientWidth, height: heightOf(index) }));
  });
  resize.observe(host);

  host.append(el('p', { class: 'sub' },
    'Hover a chart to read every value at that moment.' +
    (series.downsampled ? ' Drawn at reduced resolution; peaks are preserved.' : '')));
}

/* ---------------------------------------------------------------- splits */

const SPLITS_SHOWN = 10;

function drawSplits(host, body, topSpeed) {
  if (!body.splits.length) {
    host.append(el('div', { class: 'empty' }, 'Too short to split.'));
    return;
  }

  const fastest = Math.max(...body.splits.map((s) => s.avg_speed_kmh || 0), 1);
  const withHr = body.splits.some((s) => s.avg_hr_bpm !== null && s.avg_hr_bpm !== undefined);
  const withRider = body.splits.some((s) => s.rider_wh !== null && s.rider_wh !== undefined);

  const rows = body.splits.map((split) => {
    const partial = split.distance_km < body.km * 0.95;
    const speed = split.avg_speed_kmh || 0;
    return el('tr', {},
      el('td', {}, partial ? `${fmt.km(split.distance_km)}` : String(split.index + 1)),
      el('td', {}, fmt.duration(split.moving_s || split.duration_s)),
      el('td', {}, split.avg_speed_kmh ? split.avg_speed_kmh.toFixed(1) : '—'),
      el('td', { style: 'width:30%' },
        el('div', { class: 'bar-track' }, el('div', {
          class: 'bar',
          // The same ramp as the route, against the same top speed, so a green split is a
          // green stretch of road.
          style: `width:${Math.round((speed / fastest) * 100)}%;` +
            `background:${speedColour(speed / (topSpeed || fastest))}`,
        }))),
      el('td', {}, split.discharged_wh.toFixed(1)),
      withRider ? el('td', { class: 'rider' }, split.rider_wh.toFixed(1)) : null,
      el('td', { class: 'eff' }, split.distance_km > 0.05
        ? (split.discharged_wh / split.distance_km).toFixed(1) : '—'),
      withHr ? el('td', { class: 'hr' }, split.avg_hr_bpm ?? '—') : null,
      el('td', { class: 'alt' }, fmt.metres(split.altitude_change_m)));
  });

  const tbody = el('tbody', {}, rows.slice(0, SPLITS_SHOWN));
  host.append(el('table', {},
    el('thead', {}, el('tr', {},
      el('th', {}, 'km'), el('th', {}, 'Time'), el('th', {}, 'km/h'), el('th', {}, ''),
      el('th', { title: 'Drawn from the pack' }, 'Wh'),
      withRider ? el('th', { title: 'Your own work, estimated' }, 'You Wh') : null,
      el('th', {}, 'Wh/km'), withHr ? el('th', {}, 'bpm') : null,
      el('th', {}, 'Δ alt'))),
    tbody));

  if (rows.length > SPLITS_SHOWN) {
    const more = el('button', {
      class: 'pill', type: 'button', style: 'margin-top:12px',
      onclick: () => { tbody.replaceChildren(...rows); more.remove(); },
    }, `Show all ${rows.length} splits`);
    host.append(more);
  }
}

/* ------------------------------------------------------------ companions */

function bestHeartRate(companions) {
  const measured = companions.filter((c) => c.hr_in_session > 0);
  if (!measured.length) return null;
  // If two files are attached, the figures come from whichever actually covers the ride.
  return measured.reduce((a, b) => (b.hr_in_session > a.hr_in_session ? b : a));
}

function drawCompanions(host, sessionId, companions, fromWatch) {
  host.replaceChildren(
    ...(fromWatch ? [watchNote()] : []),
    ...companions.map((companion) => companionCard(sessionId, companion)),
    attachForm(sessionId, companions.length, fromWatch));
}

function watchNote() {
  return el('div', { class: 'companion' },
    el('div', { class: 'title' }, 'Measured by the watch'),
    el('div', { class: 'match' }, icon('check'),
      el('span', {}, 'Recorded by the phone alongside the pack, on the same clock — nothing to line up.')));
}

function companionCard(sessionId, companion) {
  const status = el('span', { class: 'saving' });
  const offset = el('input', {
    type: 'number', step: '1', class: 'offset', 'aria-label': 'Offset in seconds',
    value: String(Math.round(companion.offset_ms / 1000)),
  });

  async function apply(body) {
    status.textContent = 'saving…';
    try {
      await patch(`/sessions/${sessionId}/companions/${companion.id}`, body);
      // A changed offset moves every reading on the page, so the whole view is rebuilt rather
      // than this card alone.
      route();
    } catch (error) {
      status.textContent = String(error.message || error);
    }
  }

  async function remove() {
    if (!window.confirm('Remove this file from the ride? The original goes to the trash directory.')) return;
    status.textContent = 'removing…';
    try {
      await send('DELETE', `/sessions/${sessionId}/companions/${companion.id}`);
      route();
    } catch (error) {
      status.textContent = String(error.message || error);
    }
  }

  const facts = [
    `${companion.point_count} points`,
    companion.hr_count ? `${companion.hr_count} with heart rate` : 'no heart rate in this file',
    companion.covered_s ? `covers ${fmt.duration(companion.covered_s)}` : 'no overlap with the ride',
  ];

  const weak = weakMatch(companion);
  return el('div', { class: 'companion' + (weak ? ' warn' : '') },
    el('div', { class: 'title' },
      companion.name || companion.source_name, ' ',
      companion.creator ? el('span', { class: 'badge' }, companion.creator) : null),
    el('div', { class: 'meta' }, facts.join(' · ')),
    el('div', { class: 'match' },
      icon(weak || !['manual', 'correlation'].includes(companion.offset_source) ? 'warn' : 'check'),
      el('span', {}, alignmentNote(companion))),
    el('div', { class: 'controls' },
      el('label', { class: 'offset-label' }, 'Offset ', offset, ' s'),
      el('button', {
        class: 'pill', type: 'button',
        onclick: () => apply({ offset_ms: Math.round(Number(offset.value) * 1000) }),
      }, 'Apply'),
      el('button', { class: 'pill', type: 'button', onclick: () => apply({ realign: true }) }, 'Re-align'),
      el('a', { class: 'pill', href: `${API}/sessions/${sessionId}/companions/${companion.id}/raw.gpx` }, 'GPX'),
      el('button', { class: 'pill', type: 'button', onclick: remove }, 'Remove'),
      status));
}

function weakMatch(companion) {
  return companion.offset_source === 'correlation' && companion.correlation < 0.5;
}

/* The offset is a measurement, so the page says how good a measurement it was. Silently shifting
 * someone's heart rate by twenty seconds and saying nothing about it would be worse than useless. */
function alignmentNote(companion) {
  const seconds = Math.round(companion.offset_ms / 1000);
  const shift = seconds === 0
    ? 'no shift'
    : `shifted ${Math.abs(seconds)} s ${seconds > 0 ? 'later' : 'earlier'}`;

  if (companion.offset_source === 'manual') return `Offset set by hand — ${shift}.`;
  if (companion.offset_source === 'correlation') {
    const quality = weakMatch(companion)
      ? ' The two speed traces barely agree, so check this before trusting it.'
      : '';
    return `Matched against the ride's own speed over ${fmt.duration(companion.overlap_s)}: ` +
      `${shift}, correlation ${companion.correlation.toFixed(2)}.${quality}`;
  }
  return 'Not matched — there was too little overlapping movement to compare the two. ' +
    'The file\'s own timestamps are used as they are.';
}

function attachForm(sessionId, existing, fromWatch) {
  const input = el('input', { type: 'file', accept: '.gpx,application/gpx+xml', 'aria-label': 'GPX file' });
  const status = el('span', { class: 'saving' });

  async function upload() {
    const file = input.files && input.files[0];
    if (!file) { status.textContent = 'Choose a file first.'; return; }

    const form = new FormData();
    form.append('file', file);
    status.textContent = 'uploading…';
    try {
      await send('POST', `/sessions/${sessionId}/companions`, form);
      route();
    } catch (error) {
      status.textContent = String(error.message || error);
    }
  }

  return el('div', { class: 'companion' },
    el('p', { class: 'sub', style: 'margin:0' }, fromWatch
      ? 'A GPX exported from Strava can still be attached for its cadence. The watch\'s heart rate ' +
        'stays the one charted.'
      : existing
      ? 'Attach another file — one Strava activity can cover two recordings.'
      : 'The pack knows nothing about the rider. Open the ride on Strava, choose ⋯ → Export GPX, ' +
        'and attach the file here to put heart rate on these charts. The two clocks are lined up ' +
        'automatically.'),
    el('div', { class: 'controls' },
      input,
      el('button', { class: 'pill', type: 'button', onclick: upload }, 'Attach'),
      status));
}

/* ---------------------------------------------------------------- router */

const ROUTES = [
  [/^\/$/, (root) => dashboardView(root)],
  [/^\/rides$/, (root) => listView(root, {
    ridesOnly: true,
    heading: 'Rides',
    blurb: 'Recordings that went somewhere.',
  })],
  [/^\/sessions$/, (root) => listView(root, {
    ridesOnly: false,
    heading: 'Sessions',
    blurb: 'Everything recorded, including bench and solar sessions with no route.',
  })],
  [/^\/session\/(\d+)$/, (root, id) => detailView(root, id)],
  [/^\/profile$/, (root) => profileView(root)],
];

async function route() {
  const path = (location.hash || '#/').slice(1);
  const root = document.getElementById('app');
  root.replaceChildren();

  for (const link of document.querySelectorAll('.side nav a')) {
    const section = path.startsWith('/session/') ? null : path;
    link.classList.toggle('active', link.dataset.route === section);
  }
  refreshShell();

  for (const [pattern, view] of ROUTES) {
    const match = path.match(pattern);
    if (match) {
      try {
        await view(root, ...match.slice(1));
      } catch (error) {
        root.replaceChildren(el('div', { class: 'card error' },
          el('strong', {}, 'Could not load this page'),
          el('p', { class: 'sub' }, String(error.message || error))));
      }
      return;
    }
  }

  root.replaceChildren(el('div', { class: 'empty' }, 'No such page.'));
}

/* ----------------------------------------------------------------- start */

function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  const light = document.documentElement.dataset.theme === 'light';
  const button = document.getElementById('theme');
  // Absent when a cached copy of an older index.html is paired with this script after an update.
  const label = button.querySelector('span');
  if (label) label.textContent = light ? 'Dark theme' : 'Light theme';
  button.setAttribute('aria-label', light ? 'Switch to the dark theme' : 'Switch to the light theme');
}

applyTheme(localStorage.getItem('theme'));

document.getElementById('theme').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  localStorage.setItem('theme', next);
  applyTheme(next);
  // Charts and routes take their colours at draw time, so they are drawn again.
  route();
});

window.addEventListener('hashchange', route);
route();
