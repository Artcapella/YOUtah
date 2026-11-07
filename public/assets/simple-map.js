const API = {
  config: "/api/config",
  dashboard: "/api/dashboard",
  guideAnswer: "/api/guide/answer",
  guideSearch: "/api/guide/search",
};
const DEFAULT_VIEW = { center: [-111.891, 40.7608], zoom: 11 };
const PERSONA_COOKIE = "youtah_persona";
const PERSONA_MAX_AGE_SECONDS = 60 * 60 * 24 * 180;

let map;
let allPlaces = [];
let markers = [];
let selectedId = null;
let activeCategory = "all";
let userMarker;
let fallbackStyleUsed = false;

const list = document.querySelector("#place-list");
const summary = document.querySelector("#result-summary");
const search = document.querySelector("#place-search");
const clearSearch = document.querySelector("#clear-search");
const status = document.querySelector("#map-status");
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

let persona = readPersona();
let guideBusy = false;

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

function appendGuideMessage(kind, text, extraClass = "") {
  const message = document.createElement("article");
  message.className = `guide-message ${kind} ${extraClass}`.trim();
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  message.append(paragraph);
  guideMessages.append(message);
  guideMessages.scrollTop = guideMessages.scrollHeight;
  return message;
}

function safeUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch (_) {
    return null;
  }
}

function appendGuideSources(citations) {
  if (!Array.isArray(citations) || !citations.length) return;
  const sources = document.createElement("div");
  sources.className = "guide-sources";
  citations.slice(0, 6).forEach((citation) => {
    const url = safeUrl(citation.url);
    const item = url ? document.createElement("a") : document.createElement("div");
    item.className = `guide-source${url ? "" : " no-link"}`;
    if (url) {
      item.href = url;
      item.target = "_blank";
      item.rel = "noreferrer";
    }
    const kind = document.createElement("span");
    kind.className = "source-kind";
    kind.textContent = citation.kind || "source";
    const title = document.createElement("span");
    title.className = "source-name";
    title.textContent = citation.title || citation.source_name || "Source";
    item.append(kind, title);
    sources.append(item);
  });
  guideMessages.append(sources);
  guideMessages.scrollTop = guideMessages.scrollHeight;
}

function appendPersonaNudge(text) {
  if (!text) return;
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
  const pending = appendGuideMessage("assistant", "Finding source-backed Utah ideas…", "pending");
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
    appendGuideMessage("assistant", result.answer || "I couldn’t form a guide response just now.");
    appendGuideSources(result.citations);
    appendPersonaNudge(result.persona_nudge);
  } catch (error) {
    console.error(error);
    pending.remove();
    appendGuideMessage("assistant", "I’m having trouble reaching the guide right now. You can still explore the source-backed pins on the map.");
  } finally {
    guideBusy = false;
    guideSend.disabled = false;
    guideMessages.scrollTop = guideMessages.scrollHeight;
  }
}

function groupFor(place) {
  if (["trailhead", "park"].includes(place.category)) return "outdoors";
  if (["library", "community"].includes(place.category)) return "community";
  if (place.category === "food") return "food";
  return "culture";
}

function categoryLabel(group) {
  return { outdoors: "Outdoors", community: "Community", food: "Food", culture: "Culture" }[group] || "Place";
}

function categoryIcon(group) {
  return { outdoors: "⌂", community: "●", food: "◆", culture: "✦" }[group] || "●";
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
  const source = place.source_url ? `<a href="${escapeHtml(place.source_url)}" target="_blank" rel="noreferrer">View source ↗</a>` : "";
  const popup = new maplibregl.Popup({ offset: 20, closeButton: false }).setHTML(
    `<h2>${escapeHtml(place.name)}</h2><p>${escapeHtml(place.description)}</p>${source}`
  );
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
  const style = config.maptilerKey
    ? `https://api.maptiler.com/maps/streets-v2/style.json?key=${config.maptilerKey}`
    : "https://demotiles.maplibre.org/style.json";
  map = new maplibregl.Map({
    container: "simple-map",
    style,
    center: DEFAULT_VIEW.center,
    zoom: DEFAULT_VIEW.zoom,
    bearing: 0,
    pitch: 0,
    maxPitch: 0,
    attributionControl: true,
  });
  configureInteractions();
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
    allPlaces = dashboard.places || [];
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

function wireControls() {
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
  wireControls();
  await initialiseMap();
  await loadPlaces();
}

start().catch((error) => {
  console.error(error);
  setStatus("The map could not start. Please refresh.");
});
