const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class FakeElement {
  constructor(selector) {
    this.selector = selector;
    this.listeners = {};
    this.attributes = {};
    this.dataset = {};
    this.value = "";
    this.textContent = "";
    this.innerHTML = "";
    this.className = "";
    this.disabled = false;
    this.classList = {
      values: new Set(),
      add: (...values) => values.forEach((value) => this.classList.values.add(value)),
      remove: (...values) => values.forEach((value) => this.classList.values.delete(value)),
      contains: (value) => this.classList.values.has(value),
      toggle: (value, force) => {
        const shouldAdd = force === undefined ? !this.classList.values.has(value) : force;
        if (shouldAdd) this.classList.values.add(value);
        else this.classList.values.delete(value);
        return shouldAdd;
      },
    };
  }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  setAttribute(name, value) { this.attributes[name] = value; }
  querySelectorAll() { return []; }
  async fire(type) { return this.listeners[type]?.({ target: this }); }
}

const selectors = [
  "#notice", "#workspace", "#incident-meta", "#trace", "#export-trace", "#benchmark-output",
  "#scenario", "#scenario-summary", "#start", "#step", "#run", "#health", "#inventory-state",
  "#graph", "#graph-detail", "#graph-text", "#selection", "#hypotheses", "#timeline",
  "#timeline-filter", "#plan", "#benchmark", "#benchmark-data",
];
const elements = new Map(selectors.map((selector) => [selector, new FakeElement(selector)]));
const actions = ["#start", "#step", "#run", "#benchmark", "#export-trace"].map((selector) => elements.get(selector));
const first = {
  id: "inc-first", scenario_id: "first", tenant_id: "acme", target_service_id: "checkout-api",
  summary: "First incident summary", severity: "SEV-2", status: "investigating", created_at: "2026-10-18T00:00:00Z",
  trace: [], observations: [], selections: [], hypotheses: [], action_plan: null,
  graph_context: { nodes: [], edges: [], dependency_paths: [], blast_radius_service_ids: [], dependency_inventory_complete: true, graph_mode: { mode: "fixture", connected: true } },
};
const second = { ...first, id: "inc-second", scenario_id: "second", tenant_id: "northstar", target_service_id: "identity-api", summary: "Second incident summary", severity: "SEV-3" };
const scenarios = [
  { id: "first", title: "First scenario", tenant_id: "acme", severity: "SEV-2", summary: first.summary },
  { id: "second", title: "Second scenario", tenant_id: "northstar", severity: "SEV-3", summary: second.summary },
];

globalThis.document = {
  querySelector: (selector) => {
    if (!elements.has(selector)) elements.set(selector, new FakeElement(selector));
    return elements.get(selector);
  },
  querySelectorAll: (selector) => selector === ".action" ? actions : [],
  createElement: (tag) => new FakeElement(tag),
  body: { append() {}, appendChild() {} },
};
globalThis.window = { location: { protocol: "http:" }, setInterval() {} };
globalThis.fetch = async (url, options = {}) => {
  let body;
  if (url === "/health") body = { status: "ok", graph: { mode: "fixture", connected: true, detail: "synthetic graph fixtures" } };
  else if (url === "/api/scenarios") body = scenarios;
  else if (url === "/api/incidents") body = JSON.parse(options.body).scenario_id === "first" ? first : second;
  else throw new Error(`Unexpected request: ${url}`);
  return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => body };
};

const source = fs.readFileSync("src/graphops/static/app.js", "utf8");
vm.runInThisContext(source, { filename: "app.js" });

(async () => {
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(elements.get("#health").textContent, "Fixture mode · synthetic data");
  elements.get("#scenario").value = "first";
  await elements.get("#start").fire("click");
  assert.match(elements.get("#incident-meta").innerHTML, /SEV-2/);
  assert.match(elements.get("#incident-meta").innerHTML, /First incident summary/);

  elements.get("#scenario").value = "second";
  await elements.get("#scenario").fire("change");
  assert.match(elements.get("#incident-meta").innerHTML, /No active investigation/);
  assert.equal(elements.get("#workspace").classList.contains("hidden"), true);
  assert.equal(elements.get("#export-trace").disabled, true);

  await elements.get("#start").fire("click");
  const metadata = elements.get("#incident-meta").innerHTML;
  assert.match(metadata, /SEV-3/);
  assert.match(metadata, /northstar/);
  assert.match(metadata, /Second incident summary/);
  assert.doesNotMatch(metadata, /First incident summary|SEV-2|inc-first/);
  assert.match(elements.get("#scenario-summary").textContent, /SEV-3 · northstar tenant/);
  console.log("UI scenario-switch state regression passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
