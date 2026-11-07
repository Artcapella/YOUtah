const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const now = Date.now();
const source = { status: "live", name: "Resort", url: "https://example.test/", checked_at: new Date(now).toISOString() };
function resort(overrides = {}) {
  return {
    id: "snowbird", name: "Snowbird", coordinate: { lat: 40.58, lng: -111.65 }, elevation_m: 2365,
    region: "Little Cottonwood Canyon", website: "https://www.snowbird.com/", webcams_url: "https://www.snowbird.com/webcams",
    description: "", snow_report: { source, snowfall_24h_in: 0, base_depth_in: null },
    forecast: { source, snowfall_24h_in: 1.5, snowfall_next_24h_in: 3, snowfall_next_48h_in: 4, snow_depth_in: 12, temperature_f: 10, wind_mph: 15, valid_at: source.checked_at },
    operations: { source, lifts_open: 2, lifts_total: 3, trails_open: null, trails_total: null, runs: [], lifts: [
      { name: "Tram", status: "open", wait_minutes: 0, wait_updated_at: source.checked_at },
      { name: "Peruvian", status: "open", wait_minutes: null, wait_updated_at: null },
    ] }, ...overrides,
  };
}
async function runtime(rows, fail = false) {
  const events = {};
  const sandbox = {
    window: {}, URL, AbortSignal, Date,
    document: { addEventListener: (name, fn) => { events[name] = fn; }, querySelectorAll: () => [] },
    fetch: async () => { if (fail) throw new Error("offline"); return { ok: true, json: async () => ({ generated_at: new Date().toISOString(), resorts: rows }) }; },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../public/assets/ski-layer.js"), "utf8"), sandbox);
  await sandbox.window.YOUtahSki.load();
  return { ski: sandbox.window.YOUtahSki, events, sandbox };
}

test("a reported zero remains zero and a missing resort base falls back to modeled cover", async () => {
  const r = resort();
  const { ski } = await runtime([r]);
  assert.equal(ski.snow(r).value, 0);
  assert.equal(ski.wait(r), 0);
  assert.equal(ski.activity(r), "Quiet lift lines");
  assert.match(ski.details(r), /12.0″/);
  assert.match(ski.details(r), /Not a resort base reading/);
});

test("expired and closed lift telemetry never becomes a live wait", async () => {
  const r = resort();
  r.operations.lifts[0].wait_updated_at = new Date(now - 16 * 60000).toISOString();
  const { ski } = await runtime([r]);
  assert.equal(ski.wait(r), null);
  assert.equal(ski.activity(r), "Busyness not reported");
  r.operations.lifts[0].wait_updated_at = source.checked_at;
  r.operations.lifts[0].status = "closed";
  r.operations.lifts_open = 0;
  assert.equal(ski.wait(r), null);
  assert.equal(ski.activity(r), "No lifts running");
});

test("an outage hides previously fresh waits while retaining resort geometry and links", async () => {
  const r = resort();
  const { ski, sandbox } = await runtime([r]);
  assert.equal(ski.wait(r), 0);
  sandbox.fetch = async () => { throw new Error("offline"); };
  await ski.load({ force: true });
  assert.equal(ski.wait(r), null);
  assert.equal(ski.resorts().length, 1);
  assert.match(ski.panel(), /Reports could not refresh/);
});

test("source strings and resort names are escaped and javascript URLs cannot be followed", async () => {
  const r = resort({ name: '<img src=x onerror="alert(1)">', description: '<script>alert(1)</script>' });
  r.operations.source = { ...source, name: "<svg onload=alert(1)>", url: "javascript:alert(1)" };
  const { ski } = await runtime([r]);
  const markup = ski.panel() + ski.details(r);
  assert.doesNotMatch(markup, /<img|<script|<svg|href="javascript:/);
  assert.match(markup, /&lt;img/);
});

test("comparison sorts available waits before resorts without wait telemetry", async () => {
  const a = resort({ id: "a", name: "A" });
  const b = resort({ id: "b", name: "B" });
  a.operations = { ...a.operations, lifts: [] };
  const { ski, events } = await runtime([a, b]);
  events.change({ target: { matches: (selector) => selector === "[data-ski-sort]", value: "wait" } });
  assert.equal(ski.sorted()[0].id, "b");
  // The default checkbox state is shared across both map clients.
  assert.match(ski.panel(), /data-ski-map="runs" checked/);
});

test("terrain toggles hide runs separately and mapped lift clicks show fresh matching reports", async () => {
  const r = resort();
  const { ski, sandbox, events } = await runtime([r]);
  sandbox.fetch = async () => ({ok:true,json:async()=>({type:"FeatureCollection",metadata:{osm_data_at:"2026-06-01T00:00:00Z"},features:[]})});
  let popupHtml = "", removed = false, enabled = true;
  sandbox.maplibregl = { Popup: class {
    setLngLat() { return this; }
    setHTML(value) { popupHtml = value; return this; }
    addTo() { return this; }
    remove() { removed = true; }
  }};
  const layers = new Map(), sources = new Map(), handlers = new Map(), visibility = new Map();
  const map = {
    getStyle: () => ({layers:[{type:"symbol",layout:{"text-font":["Noto Sans Regular"]}}]}),
    getSource: (id) => sources.get(id), addSource: (id, value) => sources.set(id,value),
    getLayer: (id) => layers.get(id), addLayer: (layer) => layers.set(layer.id,layer),
    on: (event,id,handler) => handlers.set(`${event}:${id}`,handler),
    setLayoutProperty: (id,property,value) => visibility.set(id,value),
    getCanvas: () => ({style:{}}),
  };
  const terrain = await ski.attachTerrain(map, () => enabled);
  assert.equal(visibility.get("ski-runs"), "visible");
  events.change({target:{matches:(selector)=>selector==="[data-ski-map]",dataset:{skiMap:"runs"},checked:false}});
  assert.equal(visibility.get("ski-runs"), "none");
  assert.equal(visibility.get("ski-lifts"), "visible");
  assert.doesNotMatch(ski.panel(), /data-ski-map="runs" checked/);
  handlers.get("click:ski-lifts")({lngLat:{lat:40.58,lng:-111.65},features:[{properties:{kind:"lift",name:"Tram",osm_id:123,lift_type:"cable_car"}}]});
  assert.match(popupHtml, /Snowbird: open/);
  assert.match(popupHtml, /0 min wait/);
  assert.match(popupHtml, /2026-06-01/);
  enabled = false; terrain.update();
  assert.equal(visibility.get("ski-lifts"), "none");
  assert.equal(removed, true);
});
