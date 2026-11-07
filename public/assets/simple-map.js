const API = { config: "/api/config", dashboard: "/api/dashboard" };
const DEFAULT_VIEW = { center: [-111.891, 40.7608], zoom: 11 };

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

function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = value || "";
  return element.innerHTML;
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
    if (first) focusPlace(first, true);
  });
  clearSearch.addEventListener("click", () => { search.value = ""; search.focus(); render(); });
  document.querySelector("#locate").addEventListener("click", useLocation);
  document.querySelector("#reset-view").addEventListener("click", () => map.flyTo({ ...DEFAULT_VIEW, essential: true }));
  document.querySelector("#zoom-in").addEventListener("click", () => map.zoomIn());
  document.querySelector("#zoom-out").addEventListener("click", () => map.zoomOut());
}

async function start() {
  wireControls();
  await initialiseMap();
  await loadPlaces();
}

start().catch((error) => {
  console.error(error);
  setStatus("The map could not start. Please refresh.");
});
