/* Shared source-aware skiing UI and MapLibre terrain overlay for both map views. */
(function () {
  "use strict";
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (url) => { try { const u = new URL(url); return ["https:", "http:"].includes(u.protocol) ? escape(u.href) : ""; } catch { return ""; } };
  const inches = (value) => Number.isFinite(value) ? `${value.toFixed(1)}″` : "—";
  const minutes = (value) => Number.isFinite(value) ? `${Math.round(value)} min` : "Not reported";
  const stamp = (value) => {
    const date = new Date(value);
    return value && Number.isFinite(+date) ? date.toLocaleString("en-US", { timeZone: "America/Denver", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }) : "Not reported";
  };
  const listeners = new Set();
  let snapshot = { status: "idle", data: null, error: "", sort: "snow" };
  let request;
  let terrainRequest;
  const terrainMaps = new Set();
  const terrainOptions = { runs: true, lifts: true, labels: true };
  const emit = () => listeners.forEach((listener) => listener(snapshot));
  const sourcesGood = (source) => source && ["live", "cached"].includes(source.status);
  const resorts = () => snapshot.data?.resorts || [];
  const find = (id) => resorts().find((r) => r.id === id);
  const freshWaits = (r) => snapshot.status !== "error" && sourcesGood(r.operations.source) && r.operations.lifts.filter((lift) => {
    const age = Date.now() - Date.parse(lift.wait_updated_at);
    return lift.status === "open" && Number.isFinite(lift.wait_minutes) && Number.isFinite(age) && age >= -300000 && age <= 900000;
  });
  function wait(r) {
    const rows = freshWaits(r) || [];
    const values = rows.map((lift) => lift.wait_minutes).sort((a, b) => a - b);
    if (!values.length) return null;
    const mid = Math.floor(values.length / 2);
    return values.length % 2 ? values[mid] : (values[mid - 1] + values[mid]) / 2;
  }
  function snow(r) {
    const report = r.snow_report;
    if (sourcesGood(report.source) && Number.isFinite(report.snowfall_24h_in)) return { value: report.snowfall_24h_in, label: "Resort report · 24h" };
    return { value: r.forecast.snowfall_24h_in, label: `${r.forecast.source.status === "stale" ? "Older model" : "Snow model"} · 24h` };
  }
  function activity(r) {
    const value = wait(r);
    if (value !== null) return value < 5 ? "Quiet lift lines" : value < 15 ? "Moderate lift lines" : "Busy lift lines";
    if (sourcesGood(r.operations.source) && r.operations.lifts_open === 0) return "No lifts running";
    return "Busyness not reported";
  }
  const liftsText = (r) => r.operations.lifts_open == null ? "Lift status unavailable" : `${r.operations.lifts_open} / ${r.operations.lifts_total} lifts open${r.operations.source.status === "stale" ? " · older report" : ""}`;
  function summary(r) { const s = snow(r); return `${inches(s.value)} new · ${s.label.toLowerCase()} · ${liftsText(r)} · ${minutes(wait(r))} wait`; }
  function sourceLine(source) {
    return `<a href="${safeUrl(source.url)}" target="_blank" rel="noopener noreferrer">${escape(source.name)}</a> · ${escape(source.status)}${source.reported_at ? ` · reported ${escape(stamp(source.reported_at))}` : ""}${source.checked_at ? ` · checked ${escape(stamp(source.checked_at))}` : ""}`;
  }
  function sorted(query = "") {
    const needle = query.trim().toLowerCase();
    const rows = resorts().filter((r) => !needle || `${r.name} ${r.region} ${r.description}`.toLowerCase().includes(needle));
    return rows.sort((a, b) => snapshot.sort === "wait"
      ? (wait(a) ?? Infinity) - (wait(b) ?? Infinity) || a.name.localeCompare(b.name)
      : snapshot.sort === "name" ? a.name.localeCompare(b.name)
        : (snow(b).value ?? -1) - (snow(a).value ?? -1) || a.name.localeCompare(b.name));
  }
  function controls() {
    return `<div class="ski-map-options" role="group" aria-label="Ski map details">
      <label><input type="checkbox" data-ski-map="runs" ${terrainOptions.runs ? "checked" : ""}> Runs</label>
      <label><input type="checkbox" data-ski-map="lifts" ${terrainOptions.lifts ? "checked" : ""}> Lifts</label>
      <label><input type="checkbox" data-ski-map="labels" ${terrainOptions.labels ? "checked" : ""}> Names</label>
    </div><div class="ski-legend" aria-label="Run difficulty legend"><span class="easy">● Easy</span><span class="intermediate">■ Intermediate</span><span class="advanced">◆ Advanced</span><span class="expert">◆◆ Expert</span><span class="lift">┄ Lift</span></div>
    <p class="ski-caption">Zoom in for runs & lifts. Tap a line for details. Mapped terrain does not indicate an open run.</p>`;
  }
  function panel({ query = "", selectedId = "" } = {}) {
    const rows = sorted(query);
    const forecastCount = resorts().filter((r) => sourcesGood(r.forecast.source)).length;
    const operationCount = resorts().filter((r) => sourcesGood(r.operations.source)).length;
    return `<section class="ski-panel-content" aria-label="Snow and skiing conditions">
      <div class="ski-panel-intro"><div><span class="ski-eyebrow">Plan a snow day</span><h2>Snow & skiing</h2></div><button type="button" class="ski-refresh" data-ski-refresh ${snapshot.status === "loading" ? "disabled" : ""}>${snapshot.status === "loading" ? "Loading…" : "↻ Refresh"}</button></div>
      <p class="ski-caption" role="status">${snapshot.status === "error" ? escape(snapshot.error) : snapshot.status === "loading" && !rows.length ? "Loading resort reports and snow forecasts…" : `${resorts().length} resorts · ${operationCount} operations feeds · ${forecastCount} snow forecasts`}</p>
      ${controls()}
      <label class="ski-sort">Compare resorts <select data-ski-sort><option value="snow" ${snapshot.sort === "snow" ? "selected" : ""}>Most new snow</option><option value="wait" ${snapshot.sort === "wait" ? "selected" : ""}>Shortest reported wait</option><option value="name" ${snapshot.sort === "name" ? "selected" : ""}>Name</option></select></label>
      <div class="ski-resort-list">${rows.map((r) => {
        const s = snow(r), w = wait(r);
        return `<button class="ski-card ${selectedId === r.id ? "selected" : ""}" type="button" data-ski-resort="${escape(r.id)}"><span class="ski-card-heading"><strong>${escape(r.name)}</strong><span aria-hidden="true">↗</span></span><span class="ski-region">${escape(r.region)}</span><span class="ski-card-metrics"><span><b>${inches(s.value)}</b><small>${escape(s.label)}</small></span><span><b>${w === null ? "—" : minutes(w)}</b><small>${w === null ? "Wait not reported" : "Median reported wait"}</small></span></span><span class="ski-card-bottom"><span>${escape(liftsText(r))}</span><span>${escape(activity(r))}</span></span></button>`;
      }).join("") || `<p class="ski-caption">${snapshot.status === "loading" ? "Reports will appear here shortly." : "No matching resorts. Try a resort or canyon name, or refresh reports."}</p>`}</div>
      <p class="ski-caption">Latest reports, independent of the planner time slider. Missing waits and crowds remain “not reported.”</p>
      <p class="ski-attribution">Snow models: <a href="https://open-meteo.com/" target="_blank" rel="noopener noreferrer">Open-Meteo · CC BY 4.0</a>. Terrain: © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors · ODbL</a>.</p>
    </section>`;
  }
  function details(r) {
    const op = r.operations, report = r.snow_report, f = r.forecast, s = snow(r), w = wait(r);
    const trails = op.trails_open == null ? "Not reported" : `${op.trails_open} / ${op.trails_total} runs open`;
    const officialHome = new URL(r.website).origin;
    const noLifts = activity(r) === "No lifts running";
    const fresh = freshWaits(r) || [];
    const reportedBase = sourcesGood(report.source) && Number.isFinite(report.base_depth_in);
    return `<div class="ski-details">
      <p class="ski-region">${escape(r.region)} · Base ${Math.round(r.elevation_m * 3.28084).toLocaleString()} ft</p>
      ${r.description ? `<p class="ski-caption">${escape(r.description)}</p>` : ""}
      <div class="ski-detail-metrics"><div><b>${inches(s.value)}</b><span>New snow · 24h</span><small>${escape(s.label)}</small></div><div><b>${inches(f.snowfall_next_24h_in)}</b><span>Next 24h</span><small>Forecast model</small></div><div><b>${inches(f.snowfall_next_48h_in)}</b><span>Next 48h</span><small>Forecast model</small></div><div><b>${inches(reportedBase ? report.base_depth_in : f.snow_depth_in)}</b><span>${reportedBase ? "Resort base" : "Modeled snow cover"}</span><small>${reportedBase ? "Resort report" : "Not a resort base reading"}</small></div></div>
      <div class="ski-weather">${f.temperature_f == null ? "Temperature unavailable" : `${Math.round(f.temperature_f)}°F`} · ${f.wind_mph == null ? "Wind unavailable" : `${Math.round(f.wind_mph)} mph wind`}<small>Model at resort base · ${escape(stamp(f.valid_at))}${f.source.status === "stale" ? " · Older forecast" : ""}</small></div>
      <div class="ski-activity"><h3>${escape(activity(r))}</h3><p>${escape(liftsText(r))} · ${escape(trails)}${op.source.status === "stale" ? " · Older operations report" : ""}</p><p>${w === null ? noLifts ? "Lift waits are not shown while no lifts are running." : "No fresh lift waits published. Check the official report and webcams." : `Median wait ${minutes(w)} · longest ${minutes(Math.max(...fresh.map((l) => l.wait_minutes)))}. Based on ${fresh.length} of ${op.lifts_open} open lifts; lift-line activity is not an attendance count.`}</p></div>
      <details class="ski-lift-list"><summary>Lift status & line waits${op.lifts.length ? ` (${op.lifts.length})` : ""}</summary><ul>${op.lifts.map((l) => `<li><span>${escape(l.name)}</span><span class="ski-lift-status ${escape(l.status)}">${escape(l.status)}${fresh.includes(l) ? ` · ${minutes(l.wait_minutes)}` : ""}</span></li>`).join("") || "<li>Individual lifts are not reported by this resort feed.</li>"}</ul></details>
      ${op.runs?.length ? `<details class="ski-lift-list"><summary>Reported run status (${op.runs.length})</summary><ul>${op.runs.map((run) => `<li><span>${escape(run.name)}${run.groomed === true ? " · groomed" : ""}</span><span class="ski-lift-status ${escape(run.status)}">${escape(run.status)}</span></li>`).join("")}</ul></details>` : ""}
      <section class="ski-trip-links" aria-label="Ski trip essentials"><h3>Before you go</h3><a href="${safeUrl(r.website)}" target="_blank" rel="noopener noreferrer">Official snow & trail report ↗</a><a href="${safeUrl(r.webcams_url)}" target="_blank" rel="noopener noreferrer">Mountain webcams ↗</a><a href="${safeUrl(officialHome)}" target="_blank" rel="noopener noreferrer">Tickets, parking, rentals & lessons ↗</a><a href="https://www.google.com/maps/dir/?api=1&destination=${r.coordinate.lat},${r.coordinate.lng}" target="_blank" rel="noopener noreferrer">Driving directions ↗</a><a href="https://www.rideuta.com/Rider-Info/Ski-Service" target="_blank" rel="noopener noreferrer">Ski bus routes & schedules ↗</a><a href="https://www.udottraffic.utah.gov/" target="_blank" rel="noopener noreferrer">Canyon roads, traffic & traction restrictions ↗</a><a href="https://forecast.weather.gov/MapClick.php?lat=${r.coordinate.lat}&lon=${r.coordinate.lng}" target="_blank" rel="noopener noreferrer">National Weather Service forecast ↗</a><a href="https://utahavalanchecenter.org/" target="_blank" rel="noopener noreferrer">Avalanche forecast for your area ↗</a></section>
      <p class="ski-caption">Check operating hours, ticket availability, parking reservations and road restrictions before leaving. Run colors describe mapped difficulty; resort signs and closures govern access.</p>
      <div class="ski-provenance"><p>Operations: ${sourceLine(op.source)}</p>${report.source.status !== "unavailable" ? `<p>Snow report: ${sourceLine(report.source)}</p>` : ""}<p>Forecast: ${sourceLine(f.source)}</p></div>
    </div>`;
  }
  async function load({ force = false } = {}) {
    if (request) return request;
    if (!force && snapshot.data && Date.now() - Date.parse(snapshot.data.generated_at) < 300000) return snapshot.data;
    snapshot = { ...snapshot, status: "loading", error: "" }; emit();
    request = (async () => {
      try {
        const response = await fetch("/api/ski", { headers: { Accept: "application/json" }, signal: AbortSignal.timeout(14000) });
        if (!response.ok) throw new Error(`ski ${response.status}`);
        const data = await response.json();
        if (!Array.isArray(data.resorts) || !data.resorts.length) throw new Error("No resort reports");
        snapshot = { ...snapshot, status: "ready", data, error: "" };
      } catch (_) {
        snapshot = { ...snapshot, status: "error", error: "Reports could not refresh. Try again; older values may be shown. Fresh waits are hidden until the connection returns." };
      } finally { request = null; emit(); }
      return snapshot.data;
    })();
    return request;
  }

  const DIFFICULTY = { novice: "Easy", easy: "Easy", intermediate: "Intermediate", advanced: "Advanced", expert: "Expert", freeride: "Extreme / freeride", unknown: "Not mapped" };
  const TERRAIN_LAYERS = ["ski-run-areas", "ski-run-casing", "ski-runs", "ski-lifts", "ski-run-labels", "ski-lift-labels"];
  async function attachTerrain(map, isEnabled) {
    const options = terrainOptions;
    const state = { map, isEnabled, options, data: null, popup: null };
    // Sources may still stream after the style is ready (especially with 3D terrain).
    let styleReady = Boolean(map.getStyle()?.layers?.length);
    terrainMaps.add(state);
    function visibility() {
      for (const id of TERRAIN_LAYERS) if (map.getLayer(id)) {
        const kind = id.includes("lift") ? "lifts" : "runs";
        map.setLayoutProperty(id, "visibility", isEnabled() && options[kind] && (!id.endsWith("labels") || options.labels) ? "visible" : "none");
      }
      if (!isEnabled()) state.popup?.remove();
    }
    state.update = visibility;
    function install() {
      if (!state.data || !styleReady || map.getSource("ski-terrain")) { visibility(); return; }
      map.addSource("ski-terrain", { type: "geojson", data: state.data, attribution: 'Ski terrain © <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap contributors</a> (ODbL)' });
      const isRun = ["==", ["get", "kind"], "run"], isLift = ["==", ["get", "kind"], "lift"];
      const color = ["match", ["get", "difficulty"], "novice", "#19834a", "easy", "#19834a", "intermediate", "#2279d6", "advanced", "#202c38", "expert", "#b43255", "freeride", "#9e39a8", "#768492"];
      map.addLayer({ id: "ski-run-areas", type: "fill", source: "ski-terrain", minzoom: 10, filter: ["all", isRun, ["==", ["geometry-type"], "Polygon"]], paint: { "fill-color": color, "fill-opacity": .2 } });
      map.addLayer({ id: "ski-run-casing", type: "line", source: "ski-terrain", minzoom: 10, filter: isRun, layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#ffffff", "line-width": ["interpolate", ["linear"], ["zoom"], 10, 2.5, 15, 7], "line-opacity": .85 } });
      map.addLayer({ id: "ski-runs", type: "line", source: "ski-terrain", minzoom: 10, filter: isRun, layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": color, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1, 15, 4], "line-opacity": .95 } });
      map.addLayer({ id: "ski-lifts", type: "line", source: "ski-terrain", minzoom: 9.5, filter: isLift, paint: { "line-color": "#c24f15", "line-width": ["interpolate", ["linear"], ["zoom"], 10, 1.5, 15, 3], "line-dasharray": [2, 1] } });
      // Use fonts already present in the host style so both basemap providers can render labels.
      const hostFont = map.getStyle().layers.find((layer) => layer.type === "symbol" && Array.isArray(layer.layout?.["text-font"]))?.layout["text-font"] || ["Noto Sans Regular"];
      for (const [id, filter, tint] of [["ski-run-labels", isRun, "#26384a"], ["ski-lift-labels", isLift, "#9f3707"]]) map.addLayer({ id, type: "symbol", source: "ski-terrain", minzoom: 12.5, filter, layout: { "symbol-placement": "line", "text-field": ["get", "name"], "text-font": hostFont, "text-size": 11, "symbol-spacing": 260 }, paint: { "text-color": tint, "text-halo-color": "#ffffff", "text-halo-width": 1.8 } });
      visibility();
    }
    for (const id of ["ski-runs", "ski-lifts"]) {
      map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
      map.on("click", id, (event) => {
        if (!isEnabled()) return;
        const p = event.features?.[0]?.properties; if (!p) return;
        state.popup?.remove();
        const normalized = (name) => String(name).toLowerCase().replace(/\b(express|lift|chairlift)\b/g, "").replace(/[^a-z0-9]/g, "");
        const nearby = resorts().filter((r) => Math.abs(r.coordinate.lat - event.lngLat.lat) < .12 && Math.abs(r.coordinate.lng - event.lngLat.lng) < .15);
        const matches = nearby.flatMap((r) => (p.kind === "lift" ? r.operations.lifts : r.operations.runs || []).filter((item) => p.name && normalized(item.name) === normalized(p.name)).map((item) => ({ resort: r, item })));
        const match = matches.length === 1 ? matches[0] : null;
        const report = match ? `<p><b>${escape(match.resort.name)}: ${escape(match.item.status)}</b>${match.resort.operations.source.status === "stale" ? " · older report" : ""}${p.kind === "lift" && (freshWaits(match.resort) || []).includes(match.item) ? ` · ${minutes(match.item.wait_minutes)} wait` : ""}</p>` : "";
        state.popup = new maplibregl.Popup({ maxWidth: "300px", className: "ski-feature-popup" }).setLngLat(event.lngLat).setHTML(`<div class="ski-terrain-popup"><h2>${escape(p.name || (p.kind === "lift" ? "Mapped lift" : "Unnamed run"))}</h2><p>${p.kind === "run" ? `${escape(DIFFICULTY[p.difficulty] || "Not mapped")} · ${escape(p.grooming || "Grooming not mapped")}` : escape(String(p.lift_type || "Lift").replaceAll("_", " "))}${p.length_m ? ` · ${(p.length_m / 1609.344).toFixed(2)} mi` : ""}</p>${report}${p.lit === "yes" ? "<p>Night skiing mapped; verify tonight’s hours.</p>" : ""}<p>Mapped terrain does not indicate current opening. Check the resort’s report and signs for closures.</p><p>OSM map data: ${escape(state.data.metadata?.osm_data_at?.slice(0, 10) || "Date unavailable")}</p><a href="https://www.openstreetmap.org/way/${encodeURIComponent(p.osm_id)}" target="_blank" rel="noopener noreferrer">OpenStreetMap source ↗</a></div>`).addTo(map);
      });
    }
    map.on("style.load", () => { styleReady = true; install(); });
    map.on("load", () => { styleReady = true; install(); });
    if (!terrainRequest) terrainRequest = fetch("/assets/ski-terrain.geojson").then((r) => { if (!r.ok) throw new Error("Terrain unavailable"); return r.json(); }).catch((error) => { terrainRequest = null; throw error; });
    try { state.data = await terrainRequest; install(); }
    catch (_) { document.querySelectorAll(".ski-caption").forEach((p) => { if (p.textContent.startsWith("Zoom in")) p.textContent = "Ski terrain could not load. Refresh to retry; official resort maps are linked in each report."; }); }
    return state;
  }

  document.addEventListener("click", (event) => { if (event.target.closest("[data-ski-refresh]")) load({ force: true }); });
  document.addEventListener("change", (event) => {
    if (event.target.matches("[data-ski-sort]")) { snapshot = { ...snapshot, sort: event.target.value }; emit(); }
    if (event.target.matches("[data-ski-map]")) terrainMaps.forEach((state) => { state.options[event.target.dataset.skiMap] = event.target.checked; state.update(); });
  });
  window.YOUtahSki = {
    load, find, resorts, sorted, snow, wait, activity, liftsText, summary, panel, details, escape, inches, attachTerrain,
    updateTerrain: () => terrainMaps.forEach((state) => state.update()),
    subscribe: (callback) => { listeners.add(callback); return () => listeners.delete(callback); },
    get snapshot() { return snapshot; },
  };
})();
