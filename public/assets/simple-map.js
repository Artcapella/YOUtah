const API = {
  config: "/api/config",
  dashboard: "/api/dashboard",
  guideAnswer: "/api/guide/answer",
  guideSearch: "/api/guide/search",
};
const DEFAULT_VIEW = { center: [-111.891, 40.7608], zoom: 11 };
// Keep the simplified experience intentionally focused on Utah rather than
// letting a quick pan turn it into a general-purpose world map.
const UTAH_BOUNDS = [[-114.08, 36.98], [-109.02, 42.02]];
const PERSONA_COOKIE = "youtah_persona";
const PERSONA_MAX_AGE_SECONDS = 60 * 60 * 24 * 180;
const PLAN_STORAGE = "youtah_simple_plan";
const WELCOME_STORAGE = "youtah_simple_welcome_seen";
const CITY_SHORTCUTS = [
  ["Salt Lake City", -111.891, 40.7608], ["Ogden", -111.9738, 41.223],
  ["Provo", -111.6585, 40.2338], ["Park City", -111.497, 40.6461], ["Moab", -109.5498, 38.5733],
];

let map;
let allPlaces = [];
let markers = [];
let selectedId = null;
let activeCategory = "all";
let userMarker;
let fallbackStyleUsed = false;
let dashboardData = null;
let savedPlan = readPlan();
let skiEnabled = false;
let selectedSkiId = null;
let skiMarkers = [];
let skiTerrain;
let skiFitPending = false;

const list = document.querySelector("#place-list");
const summary = document.querySelector("#result-summary");
const search = document.querySelector("#place-search");
const clearSearch = document.querySelector("#clear-search");
const status = document.querySelector("#map-status");
const skiToggle = document.querySelector("#ski-layer-toggle");
const skiContent = document.querySelector("#ski-layer-content");
const guideLauncher = document.querySelector("#guide-launcher");
const guideSheet = document.querySelector("#guide-sheet");
const guideClose = document.querySelector("#guide-close");
const guideMessages = document.querySelector("#guide-messages");
const guideForm = document.querySelector("#guide-form");
const guideQuestion = document.querySelector("#guide-question");
const guideLiveWeb = document.querySelector("#guide-live-web");
const guideSend = document.querySelector("#guide-send");
const personaDialog = document.querySelector("#persona-dialog");
const personaForm = document.querySelector("#persona-form");
const personaLauncher = document.querySelector("#persona-launcher");
const personaClose = document.querySelector("#persona-close");
const personaReset = document.querySelector("#persona-reset");
const personaDot = document.querySelector("#persona-dot");
const planLauncher = document.querySelector("#plan-launcher");
const planCount = document.querySelector("#plan-count");
const featureDialog = document.querySelector("#feature-dialog");
const featureClose = document.querySelector("#feature-close");
const featureTitle = document.querySelector("#feature-title");
const featureEyebrow = document.querySelector("#feature-eyebrow");
const featureBody = document.querySelector("#feature-body");
const welcomeDialog = document.querySelector("#welcome-dialog");
const welcomeClose = document.querySelector("#welcome-close");

let persona = readPersona();
let guideBusy = false;
let personaNudgeShown = false;

function readPlan() {
  try { return JSON.parse(localStorage.getItem(PLAN_STORAGE)) || []; } catch (_) { return []; }
}

function writePlan() {
  localStorage.setItem(PLAN_STORAGE, JSON.stringify(savedPlan));
  planCount.textContent = savedPlan.length;
  planCount.hidden = savedPlan.length === 0;
}

function savePlace(place) {
  if (!savedPlan.some((item) => item.id === place.id)) {
    savedPlan.push({ id: place.id, name: place.name, description: place.description, coordinate: place.coordinate, source_url: place.source_url || null });
    writePlan();
    setStatus(`${place.name} saved to your stops`);
  }
}

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value || "";
  return element.innerHTML;
}

function readPersona() {
  const cookie = document.cookie.split("; ").find((item) => item.startsWith(`${PERSONA_COOKIE}=`));
  if (!cookie) return null;
  try {
    const value = JSON.parse(decodeURIComponent(cookie.slice(PERSONA_COOKIE.length + 1)));
    return value && typeof value === "object" ? value : null;
  } catch (_) {
    return null;
  }
}

function writePersona(value) {
  const secure = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${PERSONA_COOKIE}=${encodeURIComponent(JSON.stringify(value))}; Max-Age=${PERSONA_MAX_AGE_SECONDS}; Path=/; SameSite=Lax${secure}`;
  persona = value;
  updatePersonaUI();
}

function clearPersona() {
  document.cookie = `${PERSONA_COOKIE}=; Max-Age=0; Path=/; SameSite=Lax`;
  persona = null;
  updatePersonaUI();
}

function personaIsUseful(value) {
  if (!value || typeof value !== "object") return false;
  return ["interests", "pace", "companions", "budget", "visitor_type", "home_base", "access_needs"]
    .filter((key) => Array.isArray(value[key]) ? value[key].length : Boolean(value[key])).length >= 3;
}

function updatePersonaUI() {
  const complete = personaIsUseful(persona);
  personaDot.classList.toggle("complete", complete);
  personaDot.setAttribute("aria-label", complete ? "Personalization profile saved" : "Personalization quiz incomplete");
  personaLauncher.setAttribute("aria-label", complete ? "Edit your Utah preferences" : "Personalize your Utah guide");
}

function syncPersonaForm() {
  const value = persona || {};
  personaForm.querySelectorAll('input[type="checkbox"][name="interests"]').forEach((input) => {
    input.checked = (value.interests || []).includes(input.value);
  });
  personaForm.querySelectorAll('input[type="checkbox"][name="access_needs"]').forEach((input) => {
    input.checked = (value.access_needs || []).includes(input.value);
  });
  ["visitor_type", "pace", "companions", "budget", "home_base"].forEach((name) => {
    const input = personaForm.elements.namedItem(name);
    if (input) input.value = value[name] || "";
  });
}

function openPersona() {
  syncPersonaForm();
  if (typeof personaDialog.showModal === "function") personaDialog.showModal();
  else personaDialog.setAttribute("open", "");
}

function closePersona() {
  if (typeof personaDialog.close === "function") personaDialog.close();
  else personaDialog.removeAttribute("open");
}

function openGuide() {
  guideSheet.hidden = false;
  guideLauncher.hidden = true;
  guideLauncher.setAttribute("aria-expanded", "true");
  setTimeout(() => guideQuestion.focus(), 0);
}

function closeGuide() {
  guideSheet.hidden = true;
  guideLauncher.hidden = false;
  guideLauncher.setAttribute("aria-expanded", "false");
}

function appendGuideMessage(kind, text, extraClass = "", citations = []) {
  const message = document.createElement("article");
  message.className = `guide-message ${kind} ${extraClass}`.trim();
  if (kind === "assistant" && !extraClass) {
    message.innerHTML = YouTahGuideRenderer.renderMarkdown(text, citations);
  } else {
    const paragraph = document.createElement("p");
    paragraph.textContent = text;
    message.append(paragraph);
  }
  guideMessages.append(message);
  guideMessages.scrollTop = guideMessages.scrollHeight;
  return message;
}

function safeUrl(value) {
  return YouTahGuideRenderer.safeUrl(value);
}

function appendGuideSources(citations, message) {
  if (!Array.isArray(citations) || !citations.length) return;
  const sources = document.createElement("section");
  sources.className = "guide-sources";
  sources.setAttribute("aria-label", "Sources for this answer");
  const heading = document.createElement("h4");
  heading.textContent = "Explore the details";
  sources.append(heading);
  citations.forEach((citation, index) => {
    const url = safeUrl(citation.url);
    const item = url ? document.createElement("a") : document.createElement("div");
    item.className = `guide-source${url ? "" : " no-link"}`;
    if (url) {
      item.href = url;
      item.target = "_blank";
      item.rel = "noopener noreferrer";
    }
    const label = document.createElement("span");
    label.className = "source-label";
    label.textContent = YouTahGuideRenderer.citationLabel(citation, index);
    const copy = document.createElement("span");
    copy.className = "source-copy";
    const title = document.createElement("span");
    title.className = "source-name";
    title.textContent = citation.title || citation.source_name || "Source";
    const provider = document.createElement("span");
    provider.className = "source-provider";
    provider.textContent = `${citation.source_name || "Local source"}${url ? "" : " · Link unavailable"}`;
    copy.append(title, provider);
    item.append(label, copy);
    if (url) {
      const external = document.createElement("span");
      external.className = "source-external";
      external.textContent = "↗";
      external.setAttribute("aria-hidden", "true");
      item.append(external);
    }
    sources.append(item);
  });
  message.append(sources);
  guideMessages.scrollTop = guideMessages.scrollHeight;
}

function appendPersonaNudge(text) {
  if (!text || personaNudgeShown || personaIsUseful(persona)) return;
  personaNudgeShown = true;
  const nudge = document.createElement("p");
  nudge.className = "guide-nudge";
  nudge.textContent = `${text} `;
  const action = document.createElement("button");
  action.type = "button";
  action.className = "text-button";
  action.textContent = "Personalize";
  action.addEventListener("click", openPersona);
  nudge.append(action);
  guideMessages.append(nudge);
}

function mapPoint() {
  const center = map ? map.getCenter() : { lat: DEFAULT_VIEW.center[1], lng: DEFAULT_VIEW.center[0] };
  return { lat: center.lat, lng: center.lng };
}

async function askGuide(question, { open = true } = {}) {
  const trimmed = question.trim();
  if (!trimmed || guideBusy) return;
  if (open) openGuide();
  guideBusy = true;
  guideSend.disabled = true;
  appendGuideMessage("user", trimmed);
  const pending = appendGuideMessage("assistant", "Putting some ideas together for you…", "pending");
  const point = mapPoint();
  try {
    const response = await fetch(API.guideAnswer, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: trimmed,
        lat: point.lat,
        lng: point.lng,
        persona,
        use_live_web: Boolean(guideLiveWeb.checked),
      }),
    });
    if (!response.ok) throw new Error(`guide ${response.status}`);
    const result = await response.json();
    pending.remove();
    const answer = appendGuideMessage("assistant", result.answer || "I couldn’t put a plan together just now. Try a nearby town or a different day.", "", result.citations || []);
    appendGuideSources(result.citations, answer);
    appendPersonaNudge(result.persona_nudge);
    // Open long plans at their first stop; the visitor can read down to sources.
    guideMessages.scrollTop += answer.getBoundingClientRect().top - guideMessages.getBoundingClientRect().top - 12;
  } catch (error) {
    console.error(error);
    pending.remove();
    appendGuideMessage("assistant", "I’m having trouble connecting right now. Give me another try in a moment — or explore a few nearby pins while you wait.");
  } finally {
    guideBusy = false;
    guideSend.disabled = false;
  }
}

function groupFor(place) {
  if (place.category === "event") return "events";
  if (["trailhead", "park"].includes(place.category)) return "outdoors";
  if (["library", "community"].includes(place.category)) return "community";
  if (place.category === "food") return "food";
  return "culture";
}

function categoryLabel(group) {
  return { outdoors: "Outdoors", community: "Community", food: "Food", culture: "Culture", events: "Event" }[group] || "Place";
}

function categoryIcon(group) {
  return { outdoors: "⌂", community: "●", food: "◆", culture: "✦", events: "★" }[group] || "●";
}

function queryText(place) {
  return [place.name, place.description, place.category, ...(place.tags || [])].join(" ").toLowerCase();
}

function visiblePlaces() {
  const query = search.value.trim().toLowerCase();
  return allPlaces.filter((place) => {
    const inCategory = activeCategory === "all" || groupFor(place) === activeCategory;
    return inCategory && (!query || queryText(place).includes(query));
  });
}

function setStatus(message) {
  status.textContent = message;
}

function makeMarker(place) {
  const group = groupFor(place);
  const element = document.createElement("button");
  element.className = `place-pin ${group}`;
  element.type = "button";
  element.title = place.name;
  element.setAttribute("aria-label", `Show ${place.name}`);
  element.innerHTML = `<span>${categoryIcon(group)}</span>`;
  const placeUrl = safeUrl(place.source_url);
  const source = placeUrl ? `<a href="${escapeHtml(placeUrl)}" target="_blank" rel="noopener noreferrer">View source ↗</a>` : "";
  const directions = `https://www.google.com/maps/dir/?api=1&destination=${place.coordinate.lat},${place.coordinate.lng}`;
  const popup = new maplibregl.Popup({ offset: 20, closeButton: false }).setHTML(
    `<h2>${escapeHtml(place.name)}</h2><p>${escapeHtml(place.description)}</p><p class="popup-actions"><a href="${directions}" target="_blank" rel="noopener noreferrer">Directions ↗</a> <button type="button" data-save-place="${escapeHtml(place.id)}">Save</button></p>${source}`
  );
  popup.on("open", () => popup.getElement()?.querySelector("[data-save-place]")?.addEventListener("click", () => savePlace(place)));
  const marker = new maplibregl.Marker({ element, anchor: "bottom" })
    .setLngLat([place.coordinate.lng, place.coordinate.lat])
    .setPopup(popup)
    .addTo(map);
  element.addEventListener("click", () => focusPlace(place, true));
  return marker;
}

function renderMarkers(places) {
  markers.forEach((marker) => marker.remove());
  markers = places.map(makeMarker);
  updateMarkerDensity();
}

function updateMarkerDensity() {
  if (!map) return;
  const zoom = map.getZoom();
  const scale = zoom < 9 ? 0.62 : zoom < 10.5 ? 0.78 : 1;
  markers.forEach((marker) => {
    const element = marker.getElement();
    element.style.setProperty("--pin-scale", scale);
    // At statewide zoom, preserve a useful sample of places instead of a
    // wall of pins. All matching results remain available in the sheet.
    const index = markers.indexOf(marker);
    element.hidden = zoom < 8.5 && index % 2 === 1;
  });
}

function renderList(places) {
  const searchTerm = search.value.trim();
  summary.textContent = places.length
    ? `${places.length} ${places.length === 1 ? "place" : "places"}${searchTerm ? ` for “${searchTerm}”` : " to explore"}`
    : "No places match this search.";
  list.innerHTML = places.length ? places.map((place) => {
    const group = groupFor(place);
    return `<button class="place-card ${selectedId === place.id ? "active" : ""}" data-place-id="${escapeHtml(place.id)}" data-group="${group}" type="button">
      <span class="place-icon" aria-hidden="true">${categoryIcon(group)}</span>
      <span class="place-copy"><h2>${escapeHtml(place.name)}</h2><p>${categoryLabel(group)} · ${escapeHtml(place.description)}</p></span>
    </button>`;
  }).join("") : `<p class="empty-state">Try another search or choose a different category.</p>`;
  list.querySelectorAll("[data-place-id]").forEach((card) => {
    card.addEventListener("click", () => {
      const place = allPlaces.find((item) => item.id === card.dataset.placeId);
      if (place) focusPlace(place, true);
    });
  });
}

function render() {
  const places = visiblePlaces();
  renderMarkers(places);
  renderList(places);
  clearSearch.hidden = !search.value;
  if (skiEnabled) renderSki();
}

function renderSki() {
  skiMarkers.forEach(({ marker }) => marker.remove());
  skiMarkers = [];
  if (!skiEnabled || !map) return;
  skiContent.innerHTML = YOUtahSki.panel({ query: search.value, selectedId: selectedSkiId });
  const resorts = YOUtahSki.sorted(search.value);
  for (const resort of resorts) {
    const element = document.createElement("button");
    const snow = YOUtahSki.snow(resort);
    element.className = "ski-resort-pin";
    element.type = "button";
    element.setAttribute("aria-label", `Ski report for ${resort.name}: ${YOUtahSki.summary(resort)}`);
    element.innerHTML = `<span aria-hidden="true">⛷</span><span><b>${YOUtahSki.escape(resort.name)}</b><small>${YOUtahSki.inches(snow.value)} new · 24h${snow.label.includes("model") ? " model" : ""}</small></span>`;
    const marker = new maplibregl.Marker({ element }).setLngLat([resort.coordinate.lng, resort.coordinate.lat]).addTo(map);
    element.addEventListener("click", () => focusSki(resort.id));
    skiMarkers.push({ id: resort.id, marker });
  }
  if (skiFitPending && resorts.length) {
    skiFitPending = false;
    // Start with the Cottonwoods / Park City so actual trail geometry is visible.
    map.fitBounds([[-111.72, 40.55], [-111.45, 40.68]], { padding: { top: 95, bottom: matchMedia("(max-width: 760px)").matches ? innerHeight * .47 : 55, left: matchMedia("(max-width: 760px)").matches ? 30 : 390, right: 60 }, maxZoom: 11.8 });
  }
}

function focusSki(id) {
  const resort = YOUtahSki.find(id);
  if (!resort) return;
  selectedSkiId = id;
  skiContent.innerHTML = YOUtahSki.panel({ query: search.value, selectedId: id });
  const mobile = matchMedia("(max-width: 760px)").matches;
  map.flyTo({ center: [resort.coordinate.lng, resort.coordinate.lat], zoom: 13, padding: { top: 70, bottom: mobile ? innerHeight * .46 : 30, left: mobile ? 15 : 370, right: 15 } });
  featureEyebrow.textContent = "Ski trip plan · latest report";
  featureTitle.textContent = resort.name;
  const saved = savedPlan.some((place) => place.id === `ski:${id}`);
  featureBody.innerHTML = `<button class="save-persona" id="save-ski-resort" type="button">${saved ? "✓ Resort saved" : "♡ Save resort to my stops"}</button>${YOUtahSki.details(resort)}`;
  featureBody.querySelector("#save-ski-resort").addEventListener("click", (event) => {
    if (!savedPlan.some((place) => place.id === `ski:${id}`)) {
      savedPlan.push({ id: `ski:${id}`, name: resort.name, description: resort.region, category: "ski", coordinate: resort.coordinate, source_url: resort.website });
      writePlan();
    }
    event.currentTarget.textContent = "✓ Resort saved";
  });
  featureDialog.classList.add("ski-resort-dialog");
  featureDialog.addEventListener("close", () => featureDialog.classList.remove("ski-resort-dialog"), { once: true });
  openFeature();
}

function toggleSki(enabled) {
  skiEnabled = enabled;
  skiToggle.checked = enabled;
  document.querySelector(".place-panel").classList.toggle("ski-mode", enabled);
  list.hidden = enabled;
  summary.hidden = enabled;
  document.querySelector(".category-filters").hidden = enabled;
  skiContent.hidden = !enabled;
  search.placeholder = enabled ? "Search ski resorts or canyons" : "Search Utah places";
  const advancedLink = document.querySelector('.view-switch a[href^="/map"]:not([href^="/map/simple"])');
  if (advancedLink) advancedLink.href = enabled ? "/map?ski=1" : "/map";
  if (!enabled) {
    skiMarkers.forEach(({ marker }) => marker.remove());
    skiMarkers = [];
    selectedSkiId = null;
  } else {
    skiFitPending = !YOUtahSki.snapshot.data;
    renderSki();
    YOUtahSki.load();
    if (!skiTerrain) skiTerrain = YOUtahSki.attachTerrain(map, () => skiEnabled);
  }
  YOUtahSki.updateTerrain();
}

function focusPlace(place, openPopup) {
  selectedId = place.id;
  map.flyTo({ center: [place.coordinate.lng, place.coordinate.lat], zoom: Math.max(map.getZoom(), 14), essential: true });
  if (openPopup) {
    const marker = markers.find((item) => {
      const position = item.getLngLat();
      return position.lng === place.coordinate.lng && position.lat === place.coordinate.lat;
    });
    marker?.togglePopup();
  }
  renderList(visiblePlaces());
}

function configureInteractions() {
  map.dragRotate.disable();
  map.touchZoomRotate.disableRotation();
  map.keyboard.disableRotation();
}

async function initialiseMap() {
  const config = await fetch(API.config).then((response) => response.ok ? response.json() : {}).catch(() => ({}));
  const key = config.maptilerKey || "ZVzYn0DX2lp5DAYTDqOu";
  const style = `https://api.maptiler.com/maps/streets-v2/style.json?key=${key}`;
  map = new maplibregl.Map({
    container: "simple-map",
    style,
    center: DEFAULT_VIEW.center,
    zoom: DEFAULT_VIEW.zoom,
    bearing: 0,
    pitch: 0,
    maxPitch: 0,
    maxBounds: UTAH_BOUNDS,
    minZoom: 6.4,
    attributionControl: true,
  });
  configureInteractions();
  map.on("zoom", updateMarkerDensity);
  map.on("load", () => setStatus(config.maptilerKey ? "2D street map · MapTiler" : "2D street map · demo tiles"));
  map.on("error", () => {
    if (config.maptilerKey && !fallbackStyleUsed) {
      fallbackStyleUsed = true;
      map.setStyle("https://demotiles.maplibre.org/style.json");
      setStatus("MapTiler tiles unavailable · using demo tiles");
    }
  });
}

async function loadPlaces() {
  try {
    const response = await fetch(API.dashboard);
    if (!response.ok) throw new Error(`dashboard ${response.status}`);
    const dashboard = await response.json();
    dashboardData = dashboard;
    const events = (dashboard.events || []).filter((event) => event.coordinate).map((event) => ({
      id: `event:${event.id}`, name: event.title, category: "event", coordinate: event.coordinate,
      description: `${event.venue} · ${new Date(event.starts_at).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}${event.is_free ? " · Free" : ""}`,
      tags: [event.category, event.venue, "event"], source_url: event.source_url,
    }));
    allPlaces = [...(dashboard.places || []), ...events];
    render();
  } catch (error) {
    console.error(error);
    summary.textContent = "Places are unavailable right now.";
    list.innerHTML = '<p class="empty-state">Refresh to try loading the public place sources again.</p>';
  }
}

function useLocation() {
  if (!navigator.geolocation) {
    setStatus("Location is not available in this browser");
    return;
  }
  setStatus("Finding your location…");
  navigator.geolocation.getCurrentPosition(
    ({ coords }) => {
      const point = [coords.longitude, coords.latitude];
      const [[west, south], [east, north]] = UTAH_BOUNDS;
      if (coords.longitude < west || coords.longitude > east || coords.latitude < south || coords.latitude > north) {
        setStatus("Your location is outside Utah — showing the Utah map");
        return;
      }
      userMarker?.remove();
      const element = document.createElement("div");
      element.className = "user-pin";
      userMarker = new maplibregl.Marker({ element }).setLngLat(point).addTo(map);
      map.flyTo({ center: point, zoom: 13, essential: true });
      setStatus("Showing your approximate location");
    },
    () => setStatus("Location permission was not granted"),
    { enableHighAccuracy: false, maximumAge: 300000, timeout: 8000 }
  );
}

function openFeature() {
  if (typeof featureDialog.showModal === "function") featureDialog.showModal();
  else featureDialog.setAttribute("open", "");
}

function closeFeature() {
  if (typeof featureDialog.close === "function") featureDialog.close();
  else featureDialog.removeAttribute("open");
}

function featureCard(title, copy, meta = "") {
  return `<article class="feature-card"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(copy)}</p>${meta ? `<div class="meta">${meta}</div>` : ""}</article>`;
}

function showToday() {
  const weather = dashboardData?.weather;
  const air = dashboardData?.air;
  const events = allPlaces.filter((place) => place.category === "event").slice(0, 4);
  featureEyebrow.textContent = "Your local pulse";
  featureTitle.textContent = "Today in Utah";
  const aqiClass = (air?.aqi || 0) > 100 ? "air-caution" : "air-good";
  featureBody.innerHTML = `<div class="condition-grid">
    <article class="feature-card"><h3>Weather</h3><span class="condition-value">${weather ? `${weather.temperature_f}°` : "—"}</span><p>${escapeHtml(weather?.short_forecast || "Loading conditions")}${weather?.wind ? ` · ${escapeHtml(weather.wind)}` : ""}</p></article>
    <article class="feature-card"><h3>Air quality</h3><span class="condition-value ${aqiClass}">${air ? air.aqi : "—"}</span><p>${escapeHtml(air?.category || "Loading air quality")}</p></article>
  </div>${events.length ? `<h3 class="eyebrow">Upcoming nearby</h3>${events.map((event) => `<article class="feature-card feature-row"><div><h3>${escapeHtml(event.name)}</h3><p>${escapeHtml(event.description)}</p></div><button type="button" data-feature-place="${escapeHtml(event.id)}">Map</button></article>`).join("")}` : featureCard("No events loaded yet", "Try again shortly, or ask YOUtah to find something happening near you.")}`;
  bindFeaturePlaces(); openFeature();
}

async function showAirGuide() {
  featureEyebrow.textContent = "Outside planning";
  featureTitle.textContent = "Air guide";
  featureBody.innerHTML = '<p class="feature-empty">Checking nearby clean-air options…</p>';
  openFeature();
  try {
    const point = mapPoint();
    const response = await fetch(`/api/recommendations/escape-inversion?lat=${point.lat}&lng=${point.lng}`);
    if (!response.ok) throw new Error("air guide unavailable");
    const guide = await response.json();
    const picks = (guide.recommendations || []).slice(0, 4);
    featureBody.innerHTML = featureCard("Estimated mixing cap", `${guide.inversion_estimate_m.toLocaleString()} m. Higher trailheads may offer clearer air when the valley is hazy.`, "Always check road and trail conditions.") + (picks.length ? picks.map((place) => `<article class="feature-card feature-row"><div><h3>${escapeHtml(place.name)}</h3><p>${escapeHtml(place.air_note)} · ${escapeHtml(place.travel_note)}</p></div><button type="button" data-feature-place="${escapeHtml(place.id)}">Map</button></article>`).join("") : featureCard("No trailheads found", "Try moving the map toward the area you plan to visit."));
    bindFeaturePlaces();
  } catch (error) {
    featureBody.innerHTML = featureCard("Air guide is unavailable", "Try again in a moment, or use the official conditions linked in the advanced map.");
  }
}

function showNearby() {
  featureEyebrow.textContent = "Jump to a place";
  featureTitle.textContent = "Explore around Utah";
  featureBody.innerHTML = `<p class="feature-empty">Choose a city, or use your location to center the map around you.</p><div class="city-list">${CITY_SHORTCUTS.map(([name, lng, lat]) => `<button type="button" data-city-lng="${lng}" data-city-lat="${lat}">${name}</button>`).join("")}</div>`;
  featureBody.querySelectorAll("[data-city-lng]").forEach((button) => button.addEventListener("click", () => {
    map.flyTo({ center: [Number(button.dataset.cityLng), Number(button.dataset.cityLat)], zoom: 12, essential: true }); closeFeature();
  }));
  openFeature();
}

function showPlan() {
  featureEyebrow.textContent = "A loose itinerary";
  featureTitle.textContent = "Saved stops";
  featureBody.innerHTML = savedPlan.length ? `${savedPlan.map((place) => `<article class="feature-card feature-row"><div><h3>${escapeHtml(place.name)}</h3><p>${escapeHtml(place.description || "Saved place")}</p></div><div><button type="button" data-feature-place="${escapeHtml(place.id)}">Map</button> <button type="button" data-remove-place="${escapeHtml(place.id)}">×</button></div></article>`).join("")}<a class="feature-link" target="_blank" rel="noopener noreferrer" href="https://www.google.com/maps/dir/?api=1&destination=${savedPlan.map((place) => `${place.coordinate.lat},${place.coordinate.lng}`).join("%7C")}">Open directions ↗</a>` : `<p class="feature-empty">Save places from a pin to keep a few possibilities together here.</p>`;
  featureBody.querySelectorAll("[data-remove-place]").forEach((button) => button.addEventListener("click", () => { savedPlan = savedPlan.filter((place) => place.id !== button.dataset.removePlace); writePlan(); showPlan(); }));
  bindFeaturePlaces(); openFeature();
}

function bindFeaturePlaces() {
  featureBody.querySelectorAll("[data-feature-place]").forEach((button) => button.addEventListener("click", () => {
    if (button.dataset.featurePlace.startsWith("ski:")) {
      closeFeature();
      if (!skiEnabled) toggleSki(true);
      focusSki(button.dataset.featurePlace.slice(4));
      return;
    }
    const place = allPlaces.find((item) => item.id === button.dataset.featurePlace);
    if (place) { closeFeature(); focusPlace(place, true); }
  }));
}

function showWelcome() {
  try {
    if (localStorage.getItem(WELCOME_STORAGE)) return;
    welcomeDialog.showModal?.();
  } catch (_) { /* A private browser may not expose storage. */ }
}

function wireControls() {
  skiToggle.addEventListener("change", () => toggleSki(skiToggle.checked));
  skiContent.addEventListener("click", (event) => {
    const button = event.target.closest("[data-ski-resort]");
    if (button) focusSki(button.dataset.skiResort);
  });
  YOUtahSki.subscribe(() => { if (skiEnabled) renderSki(); });
  setInterval(() => { if (skiEnabled && !document.hidden) YOUtahSki.load({ force: true }); }, 300000);
  document.querySelectorAll(".category").forEach((button) => {
    button.addEventListener("click", () => {
      activeCategory = button.dataset.category;
      document.querySelectorAll(".category").forEach((item) => item.classList.toggle("active", item === button));
      render();
    });
  });
  search.addEventListener("input", render);
  document.querySelector("#search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (skiEnabled) {
      const resort = YOUtahSki.sorted(search.value)[0];
      if (resort) focusSki(resort.id);
      return;
    }
    const first = visiblePlaces()[0];
    if (first) {
      focusPlace(first, true);
    } else if (search.value.trim()) {
      askGuide(`Help me find: ${search.value.trim()}`);
    }
  });
  clearSearch.addEventListener("click", () => { search.value = ""; search.focus(); render(); });
  document.querySelector("#locate").addEventListener("click", useLocation);
  document.querySelector("#reset-view").addEventListener("click", () => map.flyTo({ ...DEFAULT_VIEW, essential: true }));
  document.querySelector("#zoom-in").addEventListener("click", () => map.zoomIn());
  document.querySelector("#zoom-out").addEventListener("click", () => map.zoomOut());
  document.querySelector("#today-launcher").addEventListener("click", showToday);
  document.querySelector("#air-launcher").addEventListener("click", showAirGuide);
  document.querySelector("#nearby-launcher").addEventListener("click", showNearby);
  planLauncher.addEventListener("click", showPlan);
  featureClose.addEventListener("click", closeFeature);
  welcomeClose.addEventListener("click", () => {
    try { localStorage.setItem(WELCOME_STORAGE, "1"); } catch (_) { /* no-op */ }
    welcomeDialog.close?.();
  });

  guideLauncher.addEventListener("click", openGuide);
  guideClose.addEventListener("click", closeGuide);
  guideForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = guideQuestion.value;
    guideQuestion.value = "";
    askGuide(question, { open: false });
  });

  personaLauncher.addEventListener("click", openPersona);
  personaClose.addEventListener("click", closePersona);
  personaForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const formData = new FormData(personaForm);
    const value = {
      interests: formData.getAll("interests"),
      pace: formData.get("pace") || null,
      companions: formData.get("companions") || null,
      budget: formData.get("budget") || null,
      access_needs: formData.getAll("access_needs"),
      visitor_type: formData.get("visitor_type") || null,
      home_base: String(formData.get("home_base") || "").trim() || null,
    };
    writePersona(value);
    closePersona();
    setStatus("Your local taste profile is saved in this browser");
  });
  personaReset.addEventListener("click", () => {
    clearPersona();
    personaForm.reset();
    setStatus("Your local taste profile was reset");
  });
}

async function start() {
  updatePersonaUI();
  writePlan();
  wireControls();
  await initialiseMap();
  if (new URLSearchParams(location.search).get("ski") === "1") toggleSki(true);
  await loadPlaces();
  setTimeout(showWelcome, 550);
}

start().catch((error) => {
  console.error(error);
  setStatus("The map could not start. Please refresh.");
});
