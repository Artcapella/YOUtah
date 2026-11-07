/* Connect the shared skiing layer to the advanced map's registry and detail sheet. */
(function () {
  "use strict";
  const hub = window.YOUtah, ski = window.YOUtahSki;
  if (!hub || !ski) return;
  let terrain;
  let latestData;
  let wasEnabled = false;
  function sync() {
    const enabled = hub.state.layers.has("ski");
    if (enabled) {
      ski.load();
      if (!terrain) terrain = ski.attachTerrain(hub.map, () => hub.state.layers.has("ski"));
      if (!wasEnabled) document.querySelector('[data-tab="layers"]').click();
    }
    ski.updateTerrain();
    document.querySelectorAll('#viewSwitch a[href^="/map/simple"], .view-switch-m a[href^="/map/simple"]').forEach((simple) => { simple.href = enabled ? "/map/simple?ski=1" : "/map/simple"; });
    if (enabled && !wasEnabled && hub.map) {
      document.querySelector("#inv").classList.add("collapsed");
      const fit = () => hub.map.fitBounds([[-111.72, 40.55], [-111.45, 40.68]], { padding: matchMedia("(max-width:899px)").matches ? { top: 175, bottom: 220, left: 30, right: 65 } : { top: 80, bottom: 50, left: 400, right: 80 }, maxZoom: 11.8, duration: 700 });
      if (mapReady) fit(); else hub.map.once("style.load", fit);
    }
    wasEnabled = enabled;
  }
  ski.subscribe((state) => {
    if (state.data && state.data !== latestData) {
      latestData = state.data;
      hub.registerData("ski", state.data.resorts.map((r) => ({ id: r.id, name: r.name, lat: r.coordinate.lat, lng: r.coordinate.lng, elev: r.elevation_m, src: "ski-live", d: r.region, ski: r, brochure: false })));
      const hash = decodeURIComponent(location.hash.slice(1));
      if (hash.startsWith("ski:") && !hub.state.selected) hub.select(hash);
    } else {
      // Refresh status / sorting without replacing live map features.
      renderLayers();
      if (hub.state.selected?.startsWith("ski:")) renderDetail(BYID[hub.state.selected]);
    }
  });
  document.querySelector("#panel").addEventListener("click", (event) => {
    const button = event.target.closest("[data-ski-resort]");
    if (button) hub.select(`ski:${button.dataset.skiResort}`);
  });
  setInterval(() => { if (hub.state.layers.has("ski") && !document.hidden) ski.load({ force: true }); }, 300000);
  window.YOUtahSkiAdvanced = { sync };
  if (new URLSearchParams(location.search).get("ski") === "1" || decodeURIComponent(location.hash.slice(1)).startsWith("ski:")) hub.setLayer("ski", true);
  else sync();
})();
