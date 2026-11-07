const API = {
  dashboard: "/api/dashboard",
  inversion: "/api/recommendations/escape-inversion",
  brief: "/api/intelligence/brief",
  feedback: "/api/intelligence/feedback",
  config: "/api/config"
};

let map;
let markers = [];
let allPlaces = [];

function dateText(value) {
  return new Intl.DateTimeFormat("en-US", { weekday: "short", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function escapeHtml(text) {
  const node = document.createElement("div");
  node.textContent = text;
  return node.innerHTML;
}

function renderConditions(data) {
  document.querySelector("#temperature").textContent = `${data.weather.temperature_f}°`;
  document.querySelector("#forecast").textContent = `${data.weather.short_forecast} · ${data.weather.wind}`;
  document.querySelector("#aqi").textContent = data.air.aqi;
  document.querySelector("#air-category").textContent = data.air.category;
  document.querySelector("#conditions-updated").textContent = `Updated ${dateText(data.generated_at)} · weather ${data.weather.source}`;
}

function renderBrief(brief) {
  const comfort = brief.outdoor_comfort;
  const label = document.querySelector("#comfort-label");
  label.textContent = comfort.label[0].toUpperCase() + comfort.label.slice(1);
  label.className = `air-state ${comfort.label}`;
  document.querySelector("#comfort-horizon").textContent = comfort.horizon_hours;
  document.querySelector("#pulse-score").textContent = `${brief.local_pulse.score}/100`;
  const kind = comfort.model_kind === "trained_nowcast" ? "Trained nowcast" : "Rules estimate";
  const station = brief.air_station;
  document.querySelector("#brief-detail").textContent =
    `${kind} from the ${station.name} monitor (${station.distance_km} km): ${comfort.reasons[0]}. ` +
    `Pulse: ${brief.local_pulse.drivers[0]}. ${comfort.disclaimer}`;
}

function sendFeedback(entityId, entityType, eventType) {
  // Privacy-safe aggregate signal for the future engagement model: no user, IP, or location.
  const body = JSON.stringify({ entity_id: entityId, entity_type: entityType, event_type: eventType, occurred_at: new Date().toISOString() });
  fetch(API.feedback, { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true }).catch(() => {});
}

function categoryLabel(category) {
  return { trailhead: "Outdoors", park: "Outdoors", library: "Community", food: "Food", art: "Local lore", venue: "Venue", community: "Community" }[category] || category;
}

function renderPlaces(filter = "all") {
  const grid = document.querySelector("#place-grid");
  grid.innerHTML = allPlaces.map(place => `
    <article class="place-card ${filter !== "all" && place.category !== filter && !(filter === "community" && place.category === "library") ? "hidden" : ""}" data-place-id="${place.id}">
      <span class="place-category">${categoryLabel(place.category)}</span>
      <h3>${escapeHtml(place.name)}</h3>
      <p>${escapeHtml(place.description)}</p>
    </article>`).join("");
  grid.querySelectorAll(".place-card").forEach(card => card.addEventListener("click", () => {
    focusPlace(card.dataset.placeId);
    sendFeedback(card.dataset.placeId, "place", "detail_opened");
  }));
  renderMarkers(filter);
}

function focusPlace(id) {
  const place = allPlaces.find(item => item.id === id);
  if (!place || !map) return;
  map.flyTo({ center: [place.coordinate.lng, place.coordinate.lat], zoom: 12, essential: true });
}

function renderMarkers(filter = "all") {
  markers.forEach(marker => marker.remove());
  markers = [];
  if (!map) return;
  allPlaces.filter(place => filter === "all" || place.category === filter || (filter === "community" && place.category === "library")).forEach(place => {
    const element = document.createElement("button");
    element.className = `map-marker ${place.category}`;
    element.setAttribute("aria-label", place.name);
    const popup = new maplibregl.Popup({ offset: 16 }).setHTML(`<strong>${escapeHtml(place.name)}</strong><br/><span>${escapeHtml(place.description)}</span>`);
    const marker = new maplibregl.Marker({ element }).setLngLat([place.coordinate.lng, place.coordinate.lat]).setPopup(popup).addTo(map);
    element.addEventListener("click", () => focusPlace(place.id));
    markers.push(marker);
  });
}

function renderEvents(events) {
  document.querySelector("#events-grid").innerHTML = events.map(event => `
    <article class="event-card">
      <div class="event-top"><span>${escapeHtml(event.category)}</span><span>${event.is_free ? "Free" : "Ticketed"}</span></div>
      <h3>${escapeHtml(event.title)}</h3>
      <p>${escapeHtml(event.venue)} · ${dateText(event.starts_at)}</p>
      <div class="event-bottom"><span>${escapeHtml(event.source)}</span><strong>${escapeHtml(event.price_label)}</strong></div>
    </article>`).join("");
}

function renderSources(sources) {
  const list = document.querySelector("#source-list");
  if (!list) return; // The static source directory on the homepage replaced the live list.
  list.innerHTML = sources.map(source => `
    <div class="source-item"><a href="${source.href || "#"}" target="_blank" rel="noreferrer">${escapeHtml(source.label)} ↗</a><span class="source-state ${source.status}">${source.status}</span></div>`).join("");
}

function renderRecommendations(payload) {
  if (!document.querySelector("#recommendations") || !payload?.recommendations) return;
  document.querySelector("#cap-label").textContent = `Estimated cap: ${payload.inversion_estimate_m.toLocaleString()} m`;
  document.querySelector("#inversion-summary").textContent = `Today’s estimated inversion cap is ${payload.inversion_estimate_m.toLocaleString()} m. These places rise toward clearer air while keeping access in view.`;
  document.querySelector("#recommendations").innerHTML = payload.recommendations.map(item => `
    <article class="recommendation"><div class="score">${item.score}</div><div><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.air_note)} · ${escapeHtml(item.travel_note)}</p></div><div class="rec-meta">${item.access}<small>conditions →</small></div></article>`).join("");
}

async function initializeMap() {
  const config = await fetch(API.config).then(response => response.json()).catch(() => ({}));
  // OpenFreeMap is keyless street tiles; demotiles only has country outlines, which looks blank at city zoom.
  const KEYLESS_STYLE = "https://tiles.openfreemap.org/styles/liberty";
  const style = config.maptilerKey
    ? `https://api.maptiler.com/maps/outdoor-v2/style.json?key=${config.maptilerKey}`
    : KEYLESS_STYLE;
  map = new maplibregl.Map({ container: "map", style, center: [-111.8910, 40.7608], zoom: 10.25, attributionControl: true });
  map.addControl(new maplibregl.NavigationControl(), "top-right");
  let fellBack = style === KEYLESS_STYLE;
  map.on("error", (event) => {
    // A rejected MapTiler key surfaces as a 401/403 on the style or tiles; switch once to the keyless basemap.
    const status = event?.error?.status;
    if (!fellBack && (status === 401 || status === 403)) {
      fellBack = true;
      map.setStyle(KEYLESS_STYLE);
      map.once("style.load", () => renderMarkers());
    }
  });
  map.on("load", () => renderMarkers());
}

function loadBrief() {
  fetch(API.brief)
    .then(response => (response.ok ? response.json() : Promise.reject(new Error(`brief ${response.status}`))))
    .then(renderBrief)
    .catch(error => {
      console.error(error);
      document.querySelector("#comfort-label").textContent = "Unavailable";
      document.querySelector("#pulse-score").textContent = "--";
      document.querySelector("#brief-detail").textContent = "Live air sources are unavailable right now. Check air.utah.gov.";
    });
}

async function start() {
  loadBrief();
  await initializeMap();
  const [dashboardResponse, inversionResponse] = await Promise.all([
    fetch(API.dashboard).then(response => response.json()),
    fetch(API.inversion).then(response => response.json())
  ]);
  allPlaces = dashboardResponse.places;
  renderConditions(dashboardResponse);
  renderPlaces();
  renderEvents(dashboardResponse.events);
  renderSources(dashboardResponse.sources);
  renderRecommendations(inversionResponse);
  document.querySelectorAll(".filter").forEach(button => button.addEventListener("click", () => {
    document.querySelectorAll(".filter").forEach(item => item.classList.remove("active"));
    button.classList.add("active");
    renderPlaces(button.dataset.filter);
  }));
}

start().catch(error => {
  console.error(error);
  document.querySelector("#conditions-updated").textContent = "Could not reach the live dashboard. Try refreshing.";
});
