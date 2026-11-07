const STATE = { mode: "live", lat: 40.7649, lng: -111.8421, place: "University of Utah", data: null };
const COLORS = { GO: "#14964a", CAUTION: "#c87800", SKIP: "#c83232" };
let map;

function esc(text) {
  const node = document.createElement("div");
  node.textContent = String(text ?? "");
  return node.innerHTML;
}

const NEAR_KM = 80; // only show air monitors this close to the starting point
function kmBetween(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180, a = Math.sin((lat2 - lat1) * r / 2) ** 2
    + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin((lon2 - lon1) * r / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(a));
}

const directions = (lat, lon) => `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`;

/* ---------- smog overlay: same color + fade rules as backend/inversion ---------- */
const STOPS = [[0, [0, 228, 0]], [9, [0, 228, 0]], [20, [255, 255, 0]], [35.4, [255, 126, 0]], [55.4, [255, 0, 0]], [150, [143, 63, 151]]];
const lerp = (x, x0, x1, y0, y1) => y0 + (y1 - y0) * Math.min(Math.max((x - x0) / (x1 - x0), 0), 1);

const pmLevel = pm => pm <= 9 ? "Good" : pm <= 35.4 ? "Moderate" : pm <= 55.4 ? "Unhealthy for sensitive groups" : "Unhealthy";

function pmColor(pm) {
  for (let i = 1; i < STOPS.length; i++) {
    if (pm <= STOPS[i][0] || i === STOPS.length - 1) {
      const [x0, c0] = STOPS[i - 1], [x1, c1] = STOPS[i];
      return c0.map((c, k) => lerp(pm, x0, x1, c, c1[k]));
    }
  }
}

function smogImage(smog) {
  const n = smog.grid_n, small = document.createElement("canvas");
  small.width = small.height = n;
  const ctx = small.getContext("2d"), img = ctx.createImageData(n, n);
  const cells = [...smog.cells].sort((a, b) => a.lat - b.lat || a.lon - b.lon);
  cells.forEach((c, idx) => {
    const row = n - 1 - Math.floor(idx / n), col = idx % n;           // north at the top
    const edge = Math.min(row, n - 1 - row, col, n - 1 - col) / 4;    // fade toward the border
    let alpha = lerp(c.pm25, 8, 35, 0, 115);                           // lighter tint; clean air (<= 8) stays clear
    alpha *= Math.min(Math.max(1 - (c.nearest_monitor_km - 18) / 14, 0), 1); // fade only well away from monitors
    alpha *= Math.min(edge, 1);
    const [r, g, b] = pmColor(c.pm25), p = (row * n + col) * 4;
    img.data.set([r, g, b, alpha], p);
  });
  ctx.putImageData(img, 0, 0);
  const big = document.createElement("canvas");
  big.width = big.height = 540;
  const bctx = big.getContext("2d");
  bctx.imageSmoothingEnabled = true;
  bctx.imageSmoothingQuality = "high";
  bctx.drawImage(small, 0, 0, 540, 540);
  return big.toDataURL();
}

/* ---------- map ---------- */
async function initMap() {
  const config = await fetch("/api/config").then(r => r.json()).catch(() => ({}));
  const style = config.maptilerKey
    ? `https://api.maptiler.com/maps/outdoor-v2/style.json?key=${config.maptilerKey}`
    : "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json"; // free, no key; shows roads + towns
  map = new maplibregl.Map({ container: "inv-map", style, center: [-111.78, 40.64], zoom: 9.3 });
  map.addControl(new maplibregl.NavigationControl(), "top-right");
  await new Promise(resolve => map.on("load", resolve));
}

function setGeo(id, features) {
  const data = { type: "FeatureCollection", features };
  if (map.getSource(id)) map.getSource(id).setData(data);
  else map.addSource(id, { type: "geojson", data });
}

const point = (lon, lat, props) => ({ type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties: props });

function drawMap(d) {
  const [w, s, e, n] = d.smog.bbox, coords = [[w, n], [e, n], [e, s], [w, s]];
  if (d.smog.cells.length) {
    const url = smogImage(d.smog);
    if (map.getSource("smog")) map.getSource("smog").updateImage({ url, coordinates: coords });
    else {
      map.addSource("smog", { type: "image", url, coordinates: coords });
      map.addLayer({ id: "smog", type: "raster", source: "smog", paint: { "raster-opacity": 1, "raster-fade-duration": 0 } });
    }
  }

  setGeo("trailheads", d.trailheads.map(t => point(t.lon, t.lat, t)));
  setGeo("monitors", d.monitors.filter(m => kmBetween(STATE.lat, STATE.lng, m.lat, m.lon) <= NEAR_KM)
    .map(m => point(m.lon, m.lat, m)));
  setGeo("start", [point(STATE.lng, STATE.lat, { name: STATE.place })]);

  const dotLayer = (id, color, radius, stroke) => {
    if (map.getLayer(id)) return;
    map.addLayer({ id, type: "circle", source: id, paint: {
      "circle-color": color, "circle-radius": radius, "circle-stroke-color": "#fff", "circle-stroke-width": stroke } });
  };
  dotLayer("trailheads", ["match", ["get", "verdict"], "GO", COLORS.GO, "CAUTION", COLORS.CAUTION, COLORS.SKIP], 4.5, 1);
  // air monitors: colored by their PM2.5 reading (EPA colors) with the number shown on the map
  if (!map.getLayer("monitors")) {
    const pmColorExpr = ["interpolate", ["linear"], ["get", "pm25"],
      0, "#00b400", 9, "#00b400", 20, "#e6c800", 35.4, "#ff7e00", 55.4, "#e00000", 150, "#8f3f97"];
    map.addLayer({ id: "monitors", type: "circle", source: "monitors", paint: {
      "circle-color": pmColorExpr, "circle-radius": 9, "circle-stroke-color": "#fff", "circle-stroke-width": 1.8 } });
    map.addLayer({ id: "monitor-labels", type: "symbol", source: "monitors", layout: {
      "text-field": ["to-string", ["round", ["get", "pm25"]]], "text-size": 10, "text-font": ["Montserrat SemiBold"],
      "text-allow-overlap": true }, paint: { "text-color": "#fff", "text-halo-color": "rgba(0,0,0,.35)", "text-halo-width": 1 } });
  }
  dotLayer("start", "#1e5ac8", 7, 2);

  if (!map._invPopups) {
    map._invPopups = true;
    const popup = new maplibregl.Popup({ closeButton: false, offset: 10 });
    const show = (layer, html) => {
      map.on("mouseenter", layer, ev => {
        map.getCanvas().style.cursor = "pointer";
        popup.setLngLat(ev.features[0].geometry.coordinates).setHTML(html(ev.features[0].properties)).addTo(map);
      });
      map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; popup.remove(); });
    };
    show("trailheads", p => `<strong>${esc(p.name)}</strong><br>${esc(p.verdict)} · ${p.elevation_m} m<br><small>${esc(p.why)}</small>`);
    show("monitors", p => `<strong>${esc(p.name)} air monitor</strong><br>PM2.5 ${p.pm25} µg/m³ · ${pmLevel(p.pm25)}`);
    show("start", p => `<strong>${esc(p.name)}</strong><br>starting point`);
  }
}

/* ---------- panels ---------- */
function drawPanels(d) {
  const banner = document.querySelector("#banner");
  banner.className = `inv-banner ${d.headline.tone}`;
  document.querySelector("#banner-title").textContent = d.headline.title;
  document.querySelector("#banner-body").textContent = d.headline.body;

  document.querySelector("#m-pm").textContent = d.pm25 ?? "--";
  document.querySelector("#m-pm-sub").textContent = d.pm25 == null
    ? `No air monitor nearby (${d.air_from})`
    : `PM2.5 µg/m³ · EPA level: ${d.pm_level} · ${d.air_from}`;
  document.querySelector("#m-lid").textContent = d.lid.has_inversion ? `${d.lid.lid_m.toLocaleString()} m` : "None";
  document.querySelector("#m-lid-sub").textContent = d.lid.has_inversion
    ? `≈ ${Math.round(d.lid.lid_m * 3.281).toLocaleString()} ft · pollution is trapped below this`
    : (d.stations.length < 3 ? "Too few weather stations near here to check" : "Air is mixing normally — no lid");
  document.querySelector("#m-go").textContent = `${d.counts.GO} / ${d.trailheads.length}`;
  document.querySelector("#demo-note").textContent = d.mode === "demo" && !d.in_wasatch
    ? "Note: the demo simulates a Salt Lake Valley inversion, so it is most meaningful for Wasatch Front starting points." : "";

  document.querySelector("#picks-title").textContent = `Near ${STATE.place}`;
  document.querySelector("#picks").innerHTML = d.trailheads.slice(0, 3).map(t => `
    <article class="pick">
      <span class="badge ${t.verdict}">${t.verdict}</span>
      <h3>${esc(t.name)}</h3>
      <p>${t.elevation_m.toLocaleString()} m · ${t.distance_km} km away<br>${esc(t.why)}</p>
      <a href="${directions(t.lat, t.lon)}" target="_blank" rel="noreferrer">Directions →</a>
    </article>`).join("");

  STATE.page = 0;
  drawTable();
  document.querySelector("#disclaimer").textContent = d.disclaimer;
}

/* all-trailheads table, 10 per page */
const PAGE_SIZE = 10;
function drawTable() {
  const rows = STATE.data.trailheads, pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  STATE.page = Math.min(Math.max(STATE.page, 0), pages - 1);
  const slice = rows.slice(STATE.page * PAGE_SIZE, (STATE.page + 1) * PAGE_SIZE);
  document.querySelector("#th-table tbody").innerHTML = slice.map(t => `
    <tr><td><span class="badge ${t.verdict}">${t.verdict}</span></td><td>${esc(t.name)}</td><td>${t.elevation_m.toLocaleString()} m</td>
    <td>${t.distance_km} km</td><td>${esc(t.why)}</td><td><a href="${directions(t.lat, t.lon)}" target="_blank" rel="noreferrer">Map →</a></td></tr>`).join("");
  document.querySelector("#page-info").textContent = `${STATE.page + 1} / ${pages} · ${rows.length} trailheads`;
  document.querySelector("#prev").disabled = STATE.page === 0;
  document.querySelector("#next").disabled = STATE.page >= pages - 1;
}

/* temperature vs elevation scatter (SVG, no chart library) */
function profileSvg(stations, lidM) {
  const W = 640, H = 300, P = 44, rows = stations.filter(s => s.air_temp_c != null);
  if (!rows.length) return "";
  const tMin = Math.min(...rows.map(s => s.air_temp_c)) - 1, tMax = Math.max(...rows.map(s => s.air_temp_c)) + 1;
  const x = t => P + (t - tMin) / (tMax - tMin) * (W - 2 * P);
  const y = e => H - P - (e - 1200) / (3500 - 1200) * (H - 2 * P);
  const dots = rows.map(s => `<circle cx="${x(s.air_temp_c)}" cy="${y(s.elevation_m)}" r="4" fill="#28564e" fill-opacity=".7"><title>${esc(s.name)}: ${s.elevation_m.toFixed(0)} m, ${s.air_temp_c}°C</title></circle>`).join("");
  const lid = lidM ? `<line x1="${P}" x2="${W - P}" y1="${y(lidM)}" y2="${y(lidM)}" stroke="#c83232" stroke-dasharray="6 4" stroke-width="2"/><text x="${W - P}" y="${y(lidM) - 6}" text-anchor="end" font-size="12" fill="#c83232">lid ≈ ${lidM} m</text>` : "";
  const ticks = [1500, 2000, 2500, 3000, 3500].map(e => `<text x="${P - 6}" y="${y(e) + 4}" text-anchor="end" font-size="11" fill="#607171">${e}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Temperature versus elevation">
    <line x1="${P}" y1="${H - P}" x2="${W - P}" y2="${H - P}" stroke="#d9ded4"/><line x1="${P}" y1="${P / 2}" x2="${P}" y2="${H - P}" stroke="#d9ded4"/>
    ${ticks}<text x="${W / 2}" y="${H - 10}" text-anchor="middle" font-size="12" fill="#607171">Air temp (°C) → warmer</text>
    <text x="14" y="${H / 2}" font-size="12" fill="#607171" transform="rotate(-90 14 ${H / 2})" text-anchor="middle">Elevation (m)</text>
    ${dots}${lid}</svg>`;
}

/* illustration in the "What is an inversion?" section */
function drawIllustration() {
  const e = [1300, 1500, 1700, 1800, 2200, 2600, 3000];
  const normal = [9, 8, 6.5, 6, 3.5, 1, -1.5], inversion = [-6, -3, 1, 4, 1.5, -1, -3.5];
  const W = 460, H = 300, P = 40, x = t => P + (t + 8) / 20 * (W - 2 * P), y = v => H - P - (v - 1200) / 2000 * (H - 2 * P);
  const line = (ts, c) => `<polyline fill="none" stroke="${c}" stroke-width="3" points="${ts.map((t, i) => `${x(t)},${y(e[i])}`).join(" ")}"/>` +
    ts.map((t, i) => `<circle cx="${x(t)}" cy="${y(e[i])}" r="4" fill="${c}"/>`).join("");
  document.querySelector("#illustration").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Normal day versus inversion day">
    ${line(normal, "#c9ee67")}${line(inversion, "#ff8066")}
    <line x1="${P}" x2="${W - P}" y1="${y(1800)}" y2="${y(1800)}" stroke="#ff8066" stroke-dasharray="5 4"/>
    <text x="${W - P}" y="${y(1800) - 6}" text-anchor="end" font-size="12">the lid</text>
    <text x="${x(6)}" y="${y(3000)}" font-size="13" fill="#c9ee67">normal day</text>
    <text x="${x(-7.5)}" y="${y(1250) - 6}" font-size="13" fill="#ff8066">inversion day</text>
    <text x="${W / 2}" y="${H - 8}" text-anchor="middle" font-size="12">temperature → warmer</text>
    <text x="12" y="${H / 2}" font-size="12" transform="rotate(-90 12 ${H / 2})" text-anchor="middle">height ↑</text></svg>`;
}

/* 7-day outlook bars */
async function drawOutlook() {
  const box = document.querySelector("#outlook");
  try {
    const days = await fetch(`/api/inversion/outlook?lat=${STATE.lat}&lng=${STATE.lng}`).then(r => { if (!r.ok) throw new Error(); return r.json(); });
    box.innerHTML = days.map(d => {
      const h = Math.max(8, Math.min(100, (d.lid_elevation_m - 1300) / 1700 * 100));
      const label = new Date(`${d.date}T12:00`).toLocaleDateString("en-US", { weekday: "short", month: "numeric", day: "numeric" });
      return `<div class="day ${d.confidence}" title="Morning inversion ${d.morning_inversion_c}°C · afternoon mixing ${d.afternoon_mixing_m} m">
        <div class="bar ${d.risk}" style="height:${h}%">${d.risk}</div><b>${label}</b><span>lid ${d.lid_elevation_m.toLocaleString()} m</span></div>`;
    }).join("");
  } catch {
    box.innerHTML = `<p class="muted">Forecast is unavailable right now.</p>`;
  }
}

/* ---------- load + controls ---------- */
async function load() {
  document.querySelector("#banner-title").textContent = STATE.mode === "live" ? "Loading live conditions…" : "Loading inversion-day demo…";
  const d = await fetch(`/api/inversion/today?mode=${STATE.mode}&lat=${STATE.lat}&lng=${STATE.lng}`).then(r => r.json());
  if (!d.headline) throw new Error("bad response");
  STATE.data = d;
  drawPanels(d);
  drawMap(d);
  if (STATE.moved) {
    STATE.moved = false;
    drawOutlook();
    const pts = [[STATE.lng, STATE.lat], ...d.trailheads.slice(0, 3).map(t => [t.lon, t.lat])];
    const bounds = pts.reduce((b, p) => b.extend(p), new maplibregl.LngLatBounds(pts[0], pts[0]));
    map.fitBounds(bounds, { padding: 80, maxZoom: 12, duration: 900 });
  }
}

function wireControls() {
  document.querySelector("#prev").addEventListener("click", () => { STATE.page--; drawTable(); });
  document.querySelector("#next").addEventListener("click", () => { STATE.page++; drawTable(); });
  document.querySelectorAll(".seg").forEach(btn => btn.addEventListener("click", () => {
    document.querySelectorAll(".seg").forEach(b => b.classList.toggle("active", b === btn));
    STATE.mode = btn.dataset.mode;
    load();
  }));
  const start = document.querySelector("#start"), form = document.querySelector("#address-form");
  start.addEventListener("change", () => {
    form.classList.toggle("hidden", start.value !== "address");
    if (start.value === "address") return;
    [STATE.lat, STATE.lng] = start.value.split(",").map(Number);
    STATE.place = start.options[start.selectedIndex].text;
    STATE.moved = true;
    load();
  });
  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    const msg = document.querySelector("#address-msg"), address = document.querySelector("#address").value.trim();
    if (!address) return;
    msg.textContent = "Looking up…";
    const r = await fetch(`/api/geocode?address=${encodeURIComponent(address)}`);
    if (!r.ok) { msg.textContent = "Address not found."; return; }
    const hit = await r.json();
    Object.assign(STATE, { lat: hit.lat, lng: hit.lon, place: hit.address.split(",")[0].replace(/\b\w/g, c => c.toUpperCase()) });
    msg.textContent = `Found: ${hit.address}`;
    STATE.moved = true;
    load();
  });
}

async function start() {
  drawIllustration();
  wireControls();
  await initMap();
  await Promise.all([load(), drawOutlook()]);
}

start().catch(err => {
  console.error(err);
  document.querySelector("#banner-title").textContent = "Could not reach the inversion service. Try refreshing.";
});
