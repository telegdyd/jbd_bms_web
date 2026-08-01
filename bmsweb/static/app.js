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

const fmt = {
  duration(seconds) {
    seconds = Math.round(seconds || 0);
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h) return `${h}h ${String(m).padStart(2, '0')}m`;
    if (m) return `${m}m ${String(s).padStart(2, '0')}s`;
    return `${s}s`;
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
    return `${String(shifted.getUTCHours()).padStart(2, '0')}:${String(shifted.getUTCMinutes()).padStart(2, '0')}`;
  },
  day(iso) {
    const date = new Date(iso + 'T00:00:00Z');
    const today = new Date();
    const todayIso = today.toISOString().slice(0, 10);
    const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    if (iso === todayIso) return 'Today';
    if (iso === yesterday) return 'Yesterday';
    return date.toLocaleDateString(undefined, {
      weekday: 'long', year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC',
    });
  },
};

/* Google encoded polyline, for the route thumbnails in the list. */
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
  if (!encoded) return el('div', { class: 'thumb' });
  const points = decodePolyline(encoded);
  if (points.length < 2) return el('div', { class: 'thumb' });

  const lats = points.map((p) => p[0]);
  const lons = points.map((p) => p[1]);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  // Longitude degrees are shorter than latitude ones this far north; without the correction every
  // thumbnail comes out stretched sideways.
  const scale = Math.cos((((minLat + maxLat) / 2) * Math.PI) / 180);
  const width = Math.max((maxLon - minLon) * scale, 1e-9);
  const height = Math.max(maxLat - minLat, 1e-9);
  const span = Math.max(width, height);

  const path = points
    .map(([la, lo], i) => {
      const x = 42 + ((lo - (minLon + maxLon) / 2) * scale * 72) / span;
      const y = 28 - ((la - (minLat + maxLat) / 2) * 44) / span;
      return `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(' ');

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'thumb');
  svg.setAttribute('viewBox', '0 0 84 56');
  svg.innerHTML = `<path d="${path}"/>`;
  return svg;
}

function tile(label, value, note) {
  return el('div', { class: 'tile' },
    el('div', { class: 'label' }, label),
    el('div', { class: 'value' }, value, note ? el('small', {}, ' ' + note) : null));
}

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/* --------------------------------------------------------------- list views */

function sessionRow(session) {
  const distance = session.distance_km > 0.05;
  return el('a', { class: 'row', href: `#/session/${session.id}` },
    thumbnail(session.polyline),
    el('div', {},
      el('div', { class: 'title' },
        session.title || fmt.clock(session.started_at_ms, session.tz_offset_min),
        session.kind === 'ekd01' ? el('span', { class: 'badge' }, 'display') : null,
        !session.has_location ? el('span', { class: 'badge' }, 'no gps') : null),
      el('div', { class: 'meta' },
        [session.device_label, fmt.duration(session.duration_ms / 1000),
         `${session.sample_count} samples`].filter(Boolean).join(' · '))),
    el('div', { class: 'figures' },
      el('div', { class: 'big' }, distance ? `${fmt.km(session.distance_km)} km` : '—'),
      el('div', { class: 'small' },
        session.discharged_wh ? `${session.discharged_wh.toFixed(0)} Wh` : '',
        session.wh_per_km ? ` · ${session.wh_per_km.toFixed(1)} Wh/km` : '')));
}

async function listView(root, { ridesOnly, heading, blurb }) {
  const state = { kind: '', q: '' };

  const rows = el('div', { class: 'rows' });
  const search = el('input', {
    type: 'search', placeholder: 'Search titles, notes, devices',
    oninput: debounce((event) => { state.q = event.target.value.trim(); load(); }, 250),
  });
  const kind = el('select', {
    onchange: (event) => { state.kind = event.target.value; load(); },
  }, el('option', { value: '' }, 'All devices'),
     el('option', { value: 'bms' }, 'BMS'),
     el('option', { value: 'ekd01' }, 'Display'));

  root.append(
    el('h1', {}, heading),
    el('p', { class: 'sub' }, blurb),
    el('div', { class: 'controls' }, search, kind),
    rows);

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

    const children = [];
    let day = null;
    for (const session of body.sessions) {
      if (session.local_date !== day) {
        day = session.local_date;
        children.push(el('div', { class: 'day' }, fmt.day(day)));
      }
      children.push(sessionRow(session));
    }
    rows.replaceChildren(...children);
  }

  await load();
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

/* --------------------------------------------------------------- dashboard */

async function dashboardView(root) {
  const periods = { week: 7, month: 30, year: 365, all: null };
  let period = 'month';

  const tiles = el('div', { class: 'tiles' });
  const heat = el('div', { class: 'heat' });
  const recent = el('div', { class: 'rows' });

  const buttons = Object.keys(periods).map((name) =>
    el('button', {
      class: 'pill' + (name === period ? ' on' : ''), type: 'button',
      onclick: (event) => {
        period = name;
        [...event.target.parentNode.children].forEach((b) => b.classList.remove('on'));
        event.target.classList.add('on');
        loadTotals();
      },
    }, name));

  root.append(
    el('h1', {}, 'Dashboard'),
    el('p', { class: 'sub' }, 'Rides only — bench and solar sessions are under Sessions.'),
    el('div', { class: 'controls' }, buttons),
    tiles,
    el('h2', {}, 'Activity'),
    el('div', { class: 'card' }, heat),
    el('h2', {}, 'Recent'),
    recent);

  async function loadTotals() {
    const params = new URLSearchParams();
    const days = periods[period];
    if (days) params.set('since', new Date(Date.now() - days * 86400000).toISOString().slice(0, 10));

    const { totals } = await get('/stats?' + params);
    tiles.replaceChildren(
      tile('Rides', totals.sessions),
      tile('Distance', fmt.km(totals.distance_km), 'km'),
      tile('Moving', fmt.duration(totals.moving_seconds)),
      tile('Energy', totals.discharged_wh.toFixed(0), 'Wh'),
      tile('Efficiency', totals.wh_per_km ? totals.wh_per_km.toFixed(1) : '—', totals.wh_per_km ? 'Wh/km' : ''),
      tile('Top speed', totals.max_speed_kmh ? totals.max_speed_kmh.toFixed(1) : '—', 'km/h'));
  }

  async function loadHeatmap() {
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
    heat.replaceChildren(...cells);
  }

  async function loadRecent() {
    const body = await get('/sessions?limit=8');
    recent.replaceChildren(...(body.sessions.length
      ? body.sessions.map(sessionRow)
      : [el('div', { class: 'empty' }, 'Nothing recorded yet.')]));
  }

  await Promise.all([loadTotals(), loadHeatmap(), loadRecent()]);
}

/* ------------------------------------------------------------ detail view */

const CHANNELS = [
  { field: 'watts', name: 'Power', unit: 'W', colour: '--power', digits: 0, fill: true },
  { field: 'speed_kmh', name: 'Speed', unit: 'km/h', colour: '--speed', digits: 1 },
  { field: 'volts', name: 'Voltage', unit: 'V', colour: '--volts', digits: 2 },
  { field: 'soc', name: 'State of charge', unit: '%', colour: '--soc', digits: 0 },
  { field: 'alt_m', name: 'Altitude', unit: 'm', colour: '--alt', digits: 0 },
  { field: 'delta_mv', name: 'Cell spread', unit: 'mV', colour: '--spread', digits: 0 },
];

async function detailView(root, id) {
  const session = await get(`/sessions/${id}`);
  const isEkd01 = session.kind === 'ekd01';

  const title = el('h1', { contenteditable: 'true', spellcheck: 'false' },
    session.title || fmt.day(session.local_date));
  const saved = el('span', { class: 'saving' });

  title.addEventListener('blur', async () => {
    const value = title.textContent.trim();
    if (value === (session.title || fmt.day(session.local_date))) return;
    saved.textContent = 'saving…';
    await patch(`/sessions/${id}`, { title: value });
    session.title = value;
    saved.textContent = 'saved';
    setTimeout(() => { saved.textContent = ''; }, 1500);
  });
  title.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); title.blur(); }
  });

  root.append(
    el('div', { class: 'detail-head' },
      el('div', {},
        title,
        el('p', { class: 'sub' },
          [fmt.day(session.local_date),
           fmt.clock(session.started_at_ms, session.tz_offset_min),
           session.device_label,
           `${session.sample_count} samples`].filter(Boolean).join(' · '), ' ', saved)),
      el('div', { class: 'controls' },
        el('a', { class: 'pill', href: `${API}/sessions/${id}/raw.csv` },
          el('button', { class: 'pill', type: 'button' }, 'Download CSV')))));

  root.append(summaryTiles(session, isEkd01));

  if (session.gap_count) {
    root.append(el('p', { class: 'sub' },
      `${session.gap_count} dropout${session.gap_count === 1 ? '' : 's'} totalling ` +
      `${fmt.duration(session.gap_ms / 1000)} — excluded from the watt-hour totals.`));
  }

  const mapHost = session.has_location ? el('div', {}, el('div', { id: 'map' })) : null;
  if (mapHost) root.append(el('h2', {}, 'Route'), mapHost);

  const chartHost = el('div', {});
  root.append(el('h2', {}, 'Charts'), chartHost);

  const splitHost = el('div', {});
  if (session.has_location) root.append(el('h2', {}, 'Splits'), splitHost);

  const notes = el('textarea', { class: 'notes', placeholder: 'Notes about this ride…' });
  notes.value = session.notes || '';
  notes.addEventListener('blur', async () => {
    if (notes.value === (session.notes || '')) return;
    await patch(`/sessions/${id}`, { notes: notes.value });
    session.notes = notes.value;
  });
  root.append(el('h2', {}, 'Notes'), el('div', { class: 'card' }, notes));

  const wanted = CHANNELS.filter((c) => (isEkd01 ? ['speed_kmh', 'soc'].includes(c.field) : true));
  const [track, series, splitBody] = await Promise.all([
    session.has_location ? get(`/sessions/${id}/track`) : Promise.resolve(null),
    get(`/sessions/${id}/series?fields=${wanted.map((c) => c.field).join(',')}&points=3000`),
    session.has_location ? get(`/sessions/${id}/splits`) : Promise.resolve(null),
  ]);

  const marker = mapHost ? drawMap(track) : null;
  drawCharts(chartHost, series, wanted, track, marker);
  if (splitBody) drawSplits(splitHost, splitBody);
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

/* ------------------------------------------------------------------- map */

function drawMap(track) {
  const map = L.map('map', { attributionControl: true });
  const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '© OpenStreetMap',
  });
  tiles.addTo(map);

  const points = track.points;
  if (!points.length) return null;

  const latlngs = points.map((p) => [p.lat, p.lon]);
  map.fitBounds(latlngs, { padding: [24, 24] });

  /*
   * A map built while its container has no usable size stays broken: Leaflet caches the viewport,
   * loads two tiles for a box a few pixels across, and every path — route included — renders as
   * "M0 0". Two things can cause it, and they need different cures.
   *
   * A container that gets its size late is caught by the ResizeObserver. A page opened in a
   * background tab is not: hidden documents run no rendering callbacks at all, so the map is built
   * blind, and when the tab is finally shown the element's size has not *changed*, so the observer
   * stays silent. Hence also listening for the tab becoming visible.
   */
  const host = document.getElementById('map');
  let fitted = false;
  const refresh = () => {
    if (!host.isConnected) {
      document.removeEventListener('visibilitychange', refresh);
      return;
    }
    if (document.hidden || !host.clientWidth || !host.clientHeight) return;
    map.invalidateSize();
    if (!fitted) {
      map.fitBounds(latlngs, { padding: [24, 24] });
      fitted = true;
    }
  };
  new ResizeObserver(refresh).observe(host);
  document.addEventListener('visibilitychange', refresh);

  // Coloured per segment by speed. This is the cleaned-up track, not every fix: one polyline per
  // fix would be ten thousand layers on a long ride, and the map would stop panning.
  const speeds = points.map((p) => p.speed_kmh).filter((v) => v !== null && v !== undefined);
  const fastest = speeds.length ? Math.max(...speeds) : 0;
  const layer = L.layerGroup().addTo(map);

  if (fastest > 0) {
    for (let i = 0; i < latlngs.length - 1; i++) {
      const speed = points[i].speed_kmh ?? 0;
      L.polyline([latlngs[i], latlngs[i + 1]], {
        color: speedColour(speed / fastest),
        weight: 4,
        opacity: 0.95,
      }).addTo(layer);
    }
  } else {
    L.polyline(latlngs, { color: css('--accent'), weight: 4 }).addTo(layer);
  }

  L.circleMarker(latlngs[0], { radius: 6, color: '#fff', weight: 2, fillColor: '#22c55e', fillOpacity: 1 }).addTo(map);
  L.circleMarker(latlngs[latlngs.length - 1], { radius: 6, color: '#fff', weight: 2, fillColor: '#ef4444', fillOpacity: 1 }).addTo(map);

  const cursor = L.circleMarker(latlngs[0], {
    radius: 6, color: '#fff', weight: 2, fillColor: css('--accent'), fillOpacity: 1,
  });

  const tools = el('div', { class: 'map-tools' },
    el('button', {
      class: 'pill on', type: 'button',
      onclick: (event) => {
        // Tiles need the internet on whatever computer is looking. The drawn track does not, and
        // is exactly what the phone falls back to.
        if (map.hasLayer(tiles)) { map.removeLayer(tiles); event.target.classList.remove('on'); }
        else { tiles.addTo(map); tiles.bringToBack(); event.target.classList.add('on'); }
      },
    }, 'Street map'),
    el('span', { class: 'legend' }, fastest > 0 ? `slow → fast (0–${fastest.toFixed(0)} km/h)` : ''));
  host.after(tools);

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

/* ---------------------------------------------------------------- charts */

function drawCharts(host, series, channels, track, marker) {
  const seconds = series.t.map((ms) => ms / 1000);
  const present = channels.filter((c) => (series.fields[c.field] || []).some((v) => v !== null));

  if (!present.length) {
    host.append(el('div', { class: 'empty' }, 'Nothing to chart in this recording.'));
    return;
  }

  // One key ties every chart's cursor together, so a moment can be read across all of them at
  // once — the same behaviour as the app's single scrubber.
  const sync = uPlot.sync('session');
  const charts = [];

  for (const channel of present) {
    const values = series.fields[channel.field];
    const reading = el('span', { class: 'reading' });
    const box = el('div', { class: 'chart' },
      el('div', { class: 'head' },
        el('span', { class: 'name' }, channel.name),
        reading));
    host.append(box);

    const colour = css(channel.colour);
    const chart = new uPlot({
      width: host.clientWidth || 800,
      height: 110,
      cursor: {
        sync: { key: sync.key, setSeries: false },
        y: false,
        points: { show: true },
      },
      legend: { show: false },
      scales: { x: { time: true } },
      axes: [
        { stroke: css('--muted'), grid: { stroke: css('--line'), width: 1 }, ticks: { stroke: css('--line') } },
        { stroke: css('--muted'), size: 52, grid: { stroke: css('--line'), width: 1 }, ticks: { stroke: css('--line') } },
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
    for (const chart of charts) chart.setSize({ width: host.clientWidth, height: 110 });
  });
  resize.observe(host);

  host.append(el('p', { class: 'sub' },
    'Hover a chart to read every value at that moment.' +
    (series.downsampled ? ' Drawn at reduced resolution; peaks are preserved.' : '')));
}

/* ---------------------------------------------------------------- splits */

function drawSplits(host, body) {
  if (!body.splits.length) {
    host.append(el('div', { class: 'empty' }, 'Too short to split.'));
    return;
  }

  const fastest = Math.max(...body.splits.map((s) => s.avg_speed_kmh || 0), 1);

  host.append(el('div', { class: 'card' },
    el('table', {},
      el('thead', {}, el('tr', {},
        el('th', {}, 'km'),
        el('th', {}, 'Time'),
        el('th', {}, 'Speed'),
        el('th', {}, ''),
        el('th', {}, 'Wh'),
        el('th', {}, 'Wh/km'),
        el('th', {}, 'Δ alt'))),
      el('tbody', {}, body.splits.map((split) => {
        const partial = split.distance_km < body.km * 0.95;
        return el('tr', {},
          el('td', {}, partial ? `${fmt.km(split.distance_km)}` : String(split.index + 1)),
          el('td', {}, fmt.duration(split.moving_s || split.duration_s)),
          el('td', {}, split.avg_speed_kmh ? split.avg_speed_kmh.toFixed(1) : '—'),
          el('td', { style: 'width:35%' },
            el('div', {
              class: 'bar',
              style: `width:${Math.round(((split.avg_speed_kmh || 0) / fastest) * 100)}%`,
            })),
          el('td', {}, split.discharged_wh.toFixed(1)),
          el('td', {}, split.distance_km > 0.05
            ? (split.discharged_wh / split.distance_km).toFixed(1) : '—'),
          el('td', {}, fmt.metres(split.altitude_change_m)));
      })))));
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
];

async function route() {
  const path = (location.hash || '#/').slice(1);
  const root = document.getElementById('app');
  root.replaceChildren();

  for (const link of document.querySelectorAll('.top nav a')) {
    link.classList.toggle('active', link.dataset.route === path);
  }

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

const stored = localStorage.getItem('theme');
if (stored) document.documentElement.dataset.theme = stored;

document.getElementById('theme').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('theme', next);
  route();
});

window.addEventListener('hashchange', route);
route();
