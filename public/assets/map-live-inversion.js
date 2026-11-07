/* Live "Escape the Inversion" data for the /map hub (live inversion hook).
   Feeds the existing map through window.YOUtah:
     - smog lid   -> YOUtah.setCapHeight (from NWS stations, backend/inversion)
     - valley AQI -> YOUtah.setValleyAQI (Utah DAQ PM2.5 -> EPA AQI)
     - 'aq' / 'wx' layers -> live Utah DAQ monitors and NWS stations
     - 'trailheads' layer -> the team's curated trailheads + UGRC hiking trailheads (deduplicated),
        every one judged GO / CAUTION / SKIP; the map draws a colored ring and a legend explains it
     - 'aq-surface' raster: PM2.5 surface cut off above the lid, shown by the Air quality toggle
   Add ?demo=1 to the URL to see a simulated Salt Lake Valley inversion day. */
(function () {
  const SLC = { lat: 40.7649, lng: -111.8421 };
  const WASATCH = { w: -112.7, s: 39.95, e: -111.25, n: 41.95 };
  const VERDICT_COLOR = { GO: "#14964a", CAUTION: "#d98a1c", SKIP: "#c83232" };

  // ---------- helpers ----------
  const BP = [[0, 9.0, 0, 50], [9.1, 35.4, 51, 100], [35.5, 55.4, 101, 150], [55.5, 125.4, 151, 200], [125.5, 225.4, 201, 300], [225.5, 325.4, 301, 500]];
  function pmToAqi(pm) {  // EPA PM2.5 -> AQI (2024 breakpoints)
    if (pm == null) return null;
    const c = Math.floor(pm * 10) / 10;
    const b = BP.find(([lo, hi]) => c >= lo && c <= hi) || BP[BP.length - 1];
    return Math.round((b[3] - b[2]) / (b[1] - b[0]) * (Math.min(c, b[1]) - b[0]) + b[2]);
  }
  const slug = s => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const kmBetween = (a, b) => {
    const r = Math.PI / 180, x = Math.sin((b.lat - a.lat) * r / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin((b.lng - a.lng) * r / 2) ** 2;
    return 12742 * Math.asin(Math.sqrt(x));
  };
  // same rules as backend/inversion/core.py: above lid + 100 m -> GO, otherwise follow valley PM2.5 (EPA)
  const PM_NAMES = ["Good", "Moderate", "Unhealthy for sensitive groups", "Unhealthy"];
  function judge(elev, lid, pm) {
    if (lid.has_inversion && elev >= lid.lid_m + 100) return ["GO", "above the smog lid, clean air"];
    const cat = pm == null ? 1 : [9.0, 35.4, 55.4].filter(b => pm > b).length;
    return [["GO", "GO", "CAUTION", "SKIP"][cat], `valley air: ${PM_NAMES[cat]}`];
  }

  // PM2.5 surface as soft tints (amber -> orange -> muted red); clean air transparent
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
  function smogImage(smog) {
    const n = smog.grid_n, small = document.createElement("canvas");
    small.width = small.height = n;
    const ctx = small.getContext("2d"), img = ctx.createImageData(n, n);
    [...smog.cells].sort((a, b) => a.lat - b.lat || a.lon - b.lon).forEach((c, idx) => {
      const row = n - 1 - Math.floor(idx / n), col = idx % n;
      let alpha = lerp(c.pm25, 9, 55, 0, 95);
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
    if (smogUrl && !map.getSource("aq-surface")) {
      const [w, s, e, n] = payload.smog.bbox;
      map.addSource("aq-surface", { type: "image", url: smogUrl, coordinates: [[w, n], [e, n], [e, s], [w, s]] });
      map.addLayer({ id: "aq-surface", type: "raster", source: "aq-surface", paint: { "raster-opacity": 1, "raster-fade-duration": 0 } }, "slot-fill");
    }
    Y.setLayer("inversion", Y.state.layers.has("inversion"));
  }

  // legend for the trailhead colors, shown while the Trailheads layer is on
  function addLegend(Y) {
    const box = document.createElement("div");
    box.id = "verdictLegend";
    box.style.cssText = "position:fixed;left:max(50%, 640px);bottom:72px;transform:translateX(-50%);z-index:5;display:none;" +
      "background:rgba(19,42,43,.92);color:#e7efe9;border-radius:12px;padding:8px 14px;font-size:12.5px;" +
      "box-shadow:0 6px 20px rgba(0,0,0,.25);white-space:nowrap;pointer-events:none";
    const dot = c => `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${c};border:1.5px solid #fff;margin:0 5px -1px 12px"></span>`;
    box.innerHTML = `<b>Trailhead ring = clean air today</b>${dot(VERDICT_COLOR.GO)}Go${dot(VERDICT_COLOR.CAUTION)}Caution (hazy)${dot(VERDICT_COLOR.SKIP)}Skip (smoggy)`;
    document.body.appendChild(box);
    setInterval(() => { box.style.display = Y.state.layers.has("trailheads") ? "block" : "none"; }, 500);
  }

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
    const hiking = d.trailheads.filter(t => t.use !== "Mountain biking"); // hiking + multi-use only

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

      // one trailhead layer: team's curated trailheads (rich info) + UGRC hiking trailheads not already there
      const curated = Y.DATA.trailheads.map(({ layer, gid, ...x }) => x);
      const extra = hiking.map((t, i) => ({ id: "ugrc-" + slug(t.name) + "-" + i, name: t.name, lat: t.lat, lng: t.lon,
          elev: Math.round(t.elevation_m), use: t.use, src: "ugrc", pop: .35,
          basin: t.lat >= WASATCH.s && t.lat <= WASATCH.n && t.lon >= WASATCH.w && t.lon <= WASATCH.e ? "SL" : undefined }))
        .filter(t => !curated.some(c => kmBetween(c, t) < 0.5));
      const merged = [...curated, ...extra].map(t => {
        const [verdict, why] = judge(t.elev, d.lid, d.pm25);
        return { ...t, verdict, why, d: t.d || `${verdict === "GO" ? "Clean air" : verdict === "CAUTION" ? "Hazy air — take it easy" : "Smoggy air — skip today"}: ${why}.` };
      });
      Y.registerData("trailheads", merged);
    } catch (e) { console.warn("could not register live layers", e); }

    Y.setValleyAQI(pmToAqi(d.pm25));
    Y.setInversionLive({ has: d.lid.has_inversion, lid: d.lid.lid_m, reason: d.lid.reason, source: d.source, demo,
      picks: hiking.slice(0, 3), go: hiking.filter(t => t.verdict === "GO").length, total: hiking.length });
    Y.setCapHeight(d.lid.has_inversion ? d.lid.lid_m : 1200); // 1200 m = below the valley floor -> no smog lid

    addLegend(Y);
    addOverlays(Y);
    Y.map.on("style.load", () => setTimeout(() => addOverlays(Y), 0));
  });
})();
