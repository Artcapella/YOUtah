/* Live "Escape the Inversion" data for the /map hub (live inversion hook).
   Feeds the existing map through window.YOUtah and adds two overlays:
     - smog lid   -> YOUtah.setCapHeight (from ~70 NWS stations, backend/inversion)
     - valley AQI -> YOUtah.setValleyAQI (Utah DAQ PM2.5 -> EPA AQI)
     - 'aq' layer -> live Utah DAQ monitors; 'wx' layer -> live NWS stations near SLC
     - 'aq-surface' raster: EPA-colored PM2.5 surface (green -> yellow -> orange -> red), shown by the Smog lid toggle
     - 'escape-pt' circles: trailheads judged GO / CAUTION / SKIP, shown by the "Clean-air trailheads" toggle
   Add ?demo=1 to the URL to see a simulated Salt Lake Valley inversion day. */
(function () {
  const SLC = { lat: 40.7649, lng: -111.8421 };
  const VERDICT_COLOR = { GO: "#14964a", CAUTION: "#c87800", SKIP: "#c83232" };

  // ---------- helpers ----------
  const BP = [[0, 9.0, 0, 50], [9.1, 35.4, 51, 100], [35.5, 55.4, 101, 150], [55.5, 125.4, 151, 200], [125.5, 225.4, 201, 300], [225.5, 325.4, 301, 500]];
  function pmToAqi(pm) {  // EPA PM2.5 -> AQI (2024 breakpoints)
    if (pm == null) return null;
    const c = Math.floor(pm * 10) / 10;
    const b = BP.find(([lo, hi]) => c >= lo && c <= hi) || BP[BP.length - 1];
    return Math.round((b[3] - b[2]) / (b[1] - b[0]) * (Math.min(c, b[1]) - b[0]) + b[2]);
  }
  const slug = s => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const lerp = (x, x0, x1, y0, y1) => y0 + (y1 - y0) * Math.min(Math.max((x - x0) / (x1 - x0), 0), 1);
  const STOPS = [[0, [240, 200, 120]], [9, [240, 200, 120]], [20, [242, 185, 105]], [35.4, [235, 145, 80]], [55.4, [214, 96, 77]], [150, [160, 80, 110]]];
  function pmColor(pm) {
    for (let i = 1; i < STOPS.length; i++) {
      if (pm <= STOPS[i][0] || i === STOPS.length - 1) {
        const [x0, c0] = STOPS[i - 1], [x1, c1] = STOPS[i];
        return c0.map((c, k) => lerp(pm, x0, x1, c, c1[k]));
      }
    }
  }
  // EPA levels as soft tints (amber -> orange -> muted red); fades far from monitors and at the edges
  function smogImage(smog) {
    const n = smog.grid_n, small = document.createElement("canvas");
    small.width = small.height = n;
    const ctx = small.getContext("2d"), img = ctx.createImageData(n, n);
    [...smog.cells].sort((a, b) => a.lat - b.lat || a.lon - b.lon).forEach((c, idx) => {
      const row = n - 1 - Math.floor(idx / n), col = idx % n;
      let alpha = lerp(c.pm25, 9, 55, 0, 95); // good air (<= 9) stays clear; bad air only a soft tint
      alpha *= Math.min(Math.max(1 - (c.nearest_monitor_km - 18) / 14, 0), 1);
      alpha *= Math.min(Math.min(row, n - 1 - row, col, n - 1 - col) / 4, 1);
      const [r, g, b] = pmColor(c.pm25);
      img.data.set([r, g, b, alpha], (row * n + col) * 4);
    });
    ctx.putImageData(img, 0, 0);
    const big = document.createElement("canvas");
    big.width = big.height = 540;
    const bctx = big.getContext("2d");
    bctx.imageSmoothingEnabled = true; bctx.imageSmoothingQuality = "high";
    bctx.drawImage(small, 0, 0, 540, 540);
    return big.toDataURL();
  }

  // ---------- overlays (re-added after every style reload) ----------
  let payload = null, smogUrl = null;
  function addOverlays(Y) {
    const map = Y.map;
    if (!payload) return;
    // isStyleLoaded() stays false while terrain tiles stream in; we only need the style object itself
    if (!map.getStyle || !map.getStyle() || !map.getLayer("slot-point")) { setTimeout(() => addOverlays(Y), 300); return; }
    const before = id => (map.getLayer(id) ? id : undefined);
    if (smogUrl && !map.getSource("aq-surface")) {
      const [w, s, e, n] = payload.smog.bbox;
      map.addSource("aq-surface", { type: "image", url: smogUrl, coordinates: [[w, n], [e, n], [e, s], [w, s]] });
      map.addLayer({ id: "aq-surface", type: "raster", source: "aq-surface", paint: { "raster-opacity": 1, "raster-fade-duration": 0 } }, before("slot-fill"));
    }
    if (!map.getSource("escape")) {
      // clustered like the team's other point layers; points keep the GO / CAUTION / SKIP color
      map.addSource("escape", { type: "geojson", cluster: true, clusterMaxZoom: 11, clusterRadius: 42,
        data: { type: "FeatureCollection", features: payload.trailheads.map((t, i) => ({
          type: "Feature", geometry: { type: "Point", coordinates: [t.lon, t.lat] },
          properties: { ...t, gid: "escape:" + escId(t, i) } })) } });
      map.addLayer({ id: "escape-cl", type: "circle", source: "escape", filter: ["has", "point_count"], paint: {
        "circle-color": "#2f7d55", "circle-opacity": .88, "circle-radius": ["step", ["get", "point_count"], 14, 8, 18, 25, 23],
        "circle-stroke-width": 2, "circle-stroke-color": "#fff" } }, before("slot-point"));
      map.addLayer({ id: "escape-cnt", type: "symbol", source: "escape", filter: ["has", "point_count"],
        layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": ["Noto Sans Bold"], "text-size": 12, "text-allow-overlap": true },
        paint: { "text-color": "#fff" } }, before("slot-point"));
      map.addLayer({ id: "escape-pt", type: "circle", source: "escape", filter: ["!", ["has", "point_count"]], paint: {
        "circle-color": ["match", ["get", "verdict"], "GO", VERDICT_COLOR.GO, "CAUTION", VERDICT_COLOR.CAUTION, VERDICT_COLOR.SKIP],
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, 4, 12, 7], "circle-stroke-color": "#fff", "circle-stroke-width": 1.5 } }, before("slot-point"));
      wireEscape(map, Y);
    }
    // re-apply the panel toggles so our layers follow them
    Y.setLayer("inversion", Y.state.layers.has("inversion"));
    Y.setLayer("escape", Y.state.layers.has("escape"));
  }
  let wired = false;
  function wireEscape(map, Y) {
    if (wired) return; wired = true;
    const pop = new maplibregl.Popup({ closeButton: false, offset: 10, maxWidth: "260px" });
    const html = p => `<b>${esc(p.name)}</b><br><span style="color:${VERDICT_COLOR[p.verdict]};font-weight:700">${p.verdict}</span> · ${p.elevation_m} m<br><span style="opacity:.8">${esc(p.why)}</span>`;
    map.on("mouseenter", "escape-pt", e => { map.getCanvas().style.cursor = "pointer"; pop.setLngLat(e.features[0].geometry.coordinates).setHTML(html(e.features[0].properties)).addTo(map); });
    map.on("mouseleave", "escape-pt", () => { map.getCanvas().style.cursor = ""; pop.remove(); });
    map.on("click", "escape-pt", e => { pop.remove(); Y.select(e.features[0].properties.gid); });
    map.on("mouseenter", "escape-cl", () => map.getCanvas().style.cursor = "pointer");
    map.on("mouseleave", "escape-cl", () => map.getCanvas().style.cursor = "");
    map.on("click", "escape-cl", async e => {
      const f = e.features[0];
      try { const z = await map.getSource("escape").getClusterExpansionZoom(f.properties.cluster_id); map.easeTo({ center: f.geometry.coordinates, zoom: z + .3 }); } catch {}
    });
  }
  const escId = (t, i) => slug(t.name) + "-" + i;

  // ---------- load ----------
  function whenReady(fn) {
    const t = setInterval(() => { if (window.YOUtah && window.YOUtah.map) { clearInterval(t); fn(window.YOUtah); } }, 250);
  }

  whenReady(async Y => {
    const demo = new URLSearchParams(location.search).has("demo");
    try {
      payload = await fetch(`/api/inversion/today?mode=${demo ? "demo" : "live"}&lat=${SLC.lat}&lng=${SLC.lng}`).then(r => r.json());
    } catch (e) { console.warn("live inversion data unavailable; keeping demo values", e); return; }
    if (!payload || !payload.lid) return;
    const d = payload;
    if (d.smog && d.smog.cells.length) smogUrl = smogImage(d.smog);
    d.trailheads = d.trailheads.filter(t => t.use !== "Mountain biking"); // map shows hiking spots only

    try {
      Y.registerData("aq", d.monitors.map(m => ({
        id: "daq-" + slug(m.name), name: `${m.name} air monitor`, lat: m.lat, lng: m.lon,
        pm: m.pm25, aqi: pmToAqi(m.pm25), live: true, src: "daq", pop: .3,
        d: `Live Utah DAQ reading: PM2.5 ${m.pm25} µg/m³.`
      })));
      // keep the ids the header widget looks for: 'kslc' (valley) and 'alta' (mountain)
      const st = d.stations.filter(s => s.lat != null && s.air_temp_c != null);
      const altaIdx = st.reduce((best, s, i) => (/alta/i.test(s.name) && (best < 0 || s.elevation_m > st[best].elevation_m) ? i : best), -1);
      Y.registerData("wx", st.map((s, i) => ({
        id: s.stid === "KSLC" ? "kslc" : i === altaIdx ? "alta" : "nws-" + slug(s.stid || s.name),
        name: s.name, lat: s.lat, lng: s.lon, elev: Math.round(s.elevation_m), temp: Math.round(s.air_temp_c * 10) / 10,
        cond: demo ? "simulated" : "live NWS", live: true, src: "nws", pop: .2
      })));
    } catch (e) { console.warn("could not register live layers", e); }

    try {
      Y.registerData("escape", d.trailheads.map((t, i) => ({
        id: escId(t, i), name: t.name, lat: t.lat, lng: t.lon, elev: t.elevation_m, verdict: t.verdict, why: t.why,
        use: t.use, dist: t.distance_km, src: "ugrc", pop: .4,
        d: `${t.verdict === "GO" ? "Clean air" : t.verdict === "CAUTION" ? "Hazy air — take it easy" : "Smoggy air — skip today"}: ${t.why}.`
      })));
    } catch (e) { console.warn("could not register clean-air trailheads", e); }
    Y.setValleyAQI(pmToAqi(d.pm25));
    Y.setInversionLive({ has: d.lid.has_inversion, lid: d.lid.lid_m, reason: d.lid.reason, source: d.source, demo,
      picks: d.trailheads.slice(0, 3), go: d.trailheads.filter(t => t.verdict === "GO").length, total: d.trailheads.length });
    Y.setCapHeight(d.lid.has_inversion ? d.lid.lid_m : 1200); // 1200 m = below the valley floor -> no smog lid

    addOverlays(Y);
    Y.map.on("style.load", () => setTimeout(() => addOverlays(Y), 0));
  });
})();
