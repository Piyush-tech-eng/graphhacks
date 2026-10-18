const state = { incident: null, scenarios: [], graphHealth: null, busy: false };
const $ = (selector) => document.querySelector(selector);
const escape = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" })[char]);

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const isJson = response.headers.get("content-type")?.includes("application/json");
  const body = isJson ? await response.json() : {};
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

function announce(message, isError = false) {
  const notice = $("#notice");
  notice.textContent = message;
  notice.classList.toggle("error", isError);
  notice.setAttribute("role", isError ? "alert" : "status");
}

function setBusy(button, busy, label = "Working…") {
  state.busy = busy;
  if (button) {
    if (busy) {
      button.dataset.idleLabel = button.textContent;
      button.textContent = label;
      button.classList.add("is-loading");
    } else {
      button.textContent = button.dataset.idleLabel || button.textContent;
      button.classList.remove("is-loading");
      delete button.dataset.idleLabel;
    }
  }
  document.querySelectorAll(".action").forEach((item) => { item.disabled = busy; });
  if (!busy) updateControls();
}

async function perform(button, label, task, successMessage) {
  if (state.busy) return;
  setBusy(button, true, label);
  announce(label);
  try {
    const response = await task();
    if (response?.incident) render(response.incident);
    else if (response?.id && response?.scenario_id) render(response);
    if (successMessage) announce(successMessage);
    return response;
  } catch (error) {
    announce(error.message, true);
  } finally {
    setBusy(button, false);
  }
}

function clearIncident(message = "Choose a synthetic scenario to begin. No external infrastructure is changed by this demo.") {
  state.incident = null;
  $("#workspace").classList.add("hidden");
  $("#incident-meta").innerHTML = '<span class="status-label">No active investigation</span><span>Start a scenario to inspect its graph, evidence, and safety decision.</span>';
  $("#inventory-state").textContent = "Awaiting incident";
  $("#inventory-state").className = "state-pill neutral";
  $("#graph").innerHTML = "";
  $("#graph").dataset.nodes = "[]";
  $("#graph").dataset.edges = "[]";
  $("#graph-text").innerHTML = "";
  $("#graph-detail").textContent = "The graph will be loaded from the active tenant topology.";
  $("#selection").innerHTML = '<p class="empty-state">The routing explanation appears after the first diagnostic selection.</p>';
  $("#hypotheses").innerHTML = '<p class="empty-state">Hypotheses appear after the first observation.</p>';
  $("#timeline").innerHTML = '<li class="empty-state">Run a diagnostic to build the evidence chain.</li>';
  $("#plan").innerHTML = '<p class="empty-state">A proposal is created only after evidence validation and impact checks.</p>';
  $("#trace").textContent = "Start an incident to create an audit trace.";
  $("#export-trace").disabled = true;
  $("#benchmark-output").classList.add("hidden");
  updateControls();
  announce(message);
}

function updateScenarioSummary() {
  const scenario = state.scenarios.find((item) => item.id === $("#scenario").value);
  $("#scenario-summary").textContent = scenario
    ? `${scenario.severity} · ${scenario.tenant_id} tenant · ${scenario.summary} All data is synthetic; recovery is simulation-only.`
    : "All diagnostic data is synthetic. Recovery actions are simulations only.";
}

function updateControls() {
  const incident = state.incident;
  const plan = incident?.action_plan;
  const active = Boolean(incident) && !plan && incident.status !== "insufficient_evidence";
  $("#scenario").disabled = state.busy;
  $("#start").disabled = state.busy || !$("#scenario").value;
  $("#step").disabled = state.busy || !active;
  $("#run").disabled = state.busy || !active;
  $("#export-trace").disabled = state.busy || !incident;
  document.querySelectorAll("#plan [data-action]").forEach((button) => {
    button.disabled = state.busy;
  });
}

function renderHealth(health) {
  state.graphHealth = health?.graph || null;
  const badge = $("#health");
  const graph = health?.graph;
  badge.className = "badge";
  if (!graph) {
    badge.textContent = "Error · graph status unavailable";
    badge.classList.add("blocked");
  } else if (graph.mode === "fixture") {
    badge.textContent = "Fixture mode · synthetic data";
    badge.classList.add("neutral");
  } else if (graph.mode === "falkordb" && graph.connected) {
    badge.textContent = "FalkorDB · connected";
    badge.classList.add("safe");
  } else if (graph.mode === "fixture-fallback" || (graph.mode === "falkordb" && !graph.connected)) {
    badge.textContent = "FalkorDB unavailable · fixture fallback";
    badge.classList.add("warning");
  } else {
    badge.textContent = `${graph.mode} · ${graph.connected ? "connected" : "unavailable"}`;
    badge.classList.add(graph.connected ? "safe" : "warning");
  }
  badge.title = graph?.detail || "Graph backend state from the health endpoint";
}

function roleLabel(roles = []) {
  if (roles.includes("incident_target")) return "Incident target";
  if (roles.includes("action_target")) return "Proposed action target";
  if (roles.includes("selected_target")) return "Selected diagnostic target";
  if (roles.includes("upstream_dependency")) return "Upstream dependency";
  if (roles.includes("downstream_dependent")) return "Downstream dependent";
  return "Tenant service";
}

function renderGraph(incident) {
  const graph = incident.graph_context || {};
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];
  const targetId = incident.target_service_id;
  const dependencies = nodes.filter((node) => node.id !== targetId && node.roles.includes("upstream_dependency"));
  const dependents = nodes.filter((node) => node.id !== targetId && node.roles.includes("downstream_dependent"));
  const positions = new Map();
  positions.set(targetId, { x: 430, y: 150 });
  dependencies.forEach((node, index) => positions.set(node.id, { x: 700, y: 72 + index * 83 }));
  dependents.forEach((node, index) => positions.set(node.id, { x: 155, y: 72 + index * 83 }));
  nodes.forEach((node) => { if (!positions.has(node.id)) positions.set(node.id, { x: 430, y: 300 + positions.size * 50 }); });
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const blast = new Set(graph.blast_radius_service_ids || []);
  const selectedTarget = graph.selected_target_service_id;
  const cardWidth = 172;
  const cardHeight = 52;
  const edgeMarkup = edges.map((edge, index) => {
    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    if (!source || !target) return "";
    const x1 = source.x < target.x ? source.x + cardWidth / 2 : source.x - cardWidth / 2;
    const x2 = source.x < target.x ? target.x - cardWidth / 2 : target.x + cardWidth / 2;
    const y1 = source.y;
    const y2 = target.y;
    const label = `${nodeById.get(edge.source)?.name || edge.source} ${edge.type} ${nodeById.get(edge.target)?.name || edge.target}`;
    return `<g class="graph-edge" tabindex="0" role="button" data-graph-edge="${index}" aria-label="${escape(label)}"><title>${escape(label)}</title><line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" /><text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 7}" text-anchor="middle">${escape(edge.type)}</text></g>`;
  }).join("");
  const nodeMarkup = nodes.map((node) => {
    const point = positions.get(node.id);
    const classes = [node.roles.includes("incident_target") ? "incident_target" : "", node.id === selectedTarget ? "selected-target" : "", blast.has(node.id) ? "blast-radius" : ""].filter(Boolean).join(" ");
    return `<g class="graph-node ${classes}" transform="translate(${point.x - cardWidth / 2} ${point.y - cardHeight / 2})" tabindex="0" role="button" data-graph-node="${escape(node.id)}" aria-label="${escape(node.name)}, ${escape(roleLabel(node.roles))}"><title>${escape(node.name)} — ${escape(roleLabel(node.roles))}</title><rect width="${cardWidth}" height="${cardHeight}" /><text x="12" y="22">${escape(node.name)}</text><text class="node-role" x="12" y="40">${escape(roleLabel(node.roles))}${node.id === selectedTarget ? " · selected target" : ""}${blast.has(node.id) ? " · blast radius" : ""}</text></g>`;
  }).join("");
  $("#graph").innerHTML = nodes.length
    ? `<svg viewBox="0 0 860 380" role="img" aria-labelledby="graph-title graph-description"><title id="graph-title">Tenant service dependency graph</title><desc id="graph-description">Directed DEPENDS_ON edges. The incident target is centered; dependencies appear to the right and dependent services to the left.</desc><defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#8193a1" /></marker></defs>${edgeMarkup}${nodeMarkup}</svg>`
    : '<p class="empty-state">No graph nodes are available for this incident.</p>';
  const nodeList = nodes.map((node) => `<li><button class="evidence-link" type="button" data-graph-node="${escape(node.id)}">${escape(node.name)}</button> — ${escape(roleLabel(node.roles))}${blast.has(node.id) ? " · blast radius" : ""}</li>`).join("");
  const edgeList = edges.map((edge, index) => `<li><button class="evidence-link" type="button" data-graph-edge="${index}">${escape(nodeById.get(edge.source)?.name || edge.source)} ${escape(edge.type)} ${escape(nodeById.get(edge.target)?.name || edge.target)}</button></li>`).join("");
  $("#graph-text").innerHTML = `<h3>Services</h3><ul>${nodeList || "<li>No services</li>"}</ul><h3>Relationships</h3><ul>${edgeList || "<li>No dependency edges</li>"}</ul>`;
  $("#graph").dataset.edges = JSON.stringify(edges);
  $("#graph").dataset.nodes = JSON.stringify(nodes);
  const inventory = $("#inventory-state");
  inventory.textContent = graph.dependency_inventory_complete ? "Impact inventory complete" : "Impact inventory incomplete";
  inventory.className = `state-pill ${graph.dependency_inventory_complete ? "safe" : "warning"}`;
  if (!nodes.length) $("#graph-detail").textContent = "The active tenant graph returned no nodes.";
}

function describeGraphSelection(target) {
  const graph = $("#graph");
  const nodes = JSON.parse(graph.dataset.nodes || "[]");
  const edges = JSON.parse(graph.dataset.edges || "[]");
  const node = nodes.find((item) => item.id === target.dataset.graphNode);
  const edgeIndex = target.dataset.graphEdge;
  if (node) {
    const related = (state.incident.observations || []).filter((item) => item.service_id === node.id);
    const roles = [roleLabel(node.roles)];
    if (node.id === state.incident.graph_context.selected_target_service_id) roles.push("current diagnostic target");
    if ((state.incident.graph_context.blast_radius_service_ids || []).includes(node.id)) roles.push("action blast radius");
    $("#graph-detail").textContent = `${node.name} (${node.id}) · ${roles.join(" · ")} · team ${node.team} · criticality ${node.criticality}. ${related.length ? `${related.length} incident observation(s): ${related.map((item) => item.summary).join("; ")}` : "No observation is currently attached to this service."}`;
  } else if (edgeIndex !== undefined && edges[Number(edgeIndex)]) {
    const edge = edges[Number(edgeIndex)];
    const byId = new Map(nodes.map((item) => [item.id, item.name]));
    $("#graph-detail").textContent = `${byId.get(edge.source) || edge.source} ${edge.type} ${byId.get(edge.target) || edge.target}. The arrow follows the stored relationship direction: a dependent service points to the service it depends on.`;
  }
}

function renderIncidentMeta(incident) {
  $("#incident-meta").innerHTML = `<span class="status-label">${escape(incident.status.replaceAll("_", " ").toUpperCase())}</span><span>${escape(incident.id)} · ${escape(incident.tenant_id)} · ${escape(incident.severity)} · started ${escape(new Date(incident.created_at).toLocaleString())}</span><span>${escape(incident.summary)}</span>`;
}

function render(incident) {
  if (!incident?.id || !incident?.scenario_id) throw new Error("The API returned an incomplete incident view.");
  state.incident = incident;
  $("#workspace").classList.remove("hidden");
  renderIncidentMeta(incident);
  renderGraph(incident);
  renderSelection(incident);
  renderHypotheses(incident);
  renderTimeline(incident);
  renderPlan(incident.action_plan);
  $("#trace").textContent = JSON.stringify(incident.trace || [], null, 2);
  updateControls();
}

function renderSelection(incident) {
  const latest = incident.selections?.at(-1);
  if (!latest) {
    $("#selection").innerHTML = '<p class="empty-state">No tool has been selected yet. Start a diagnostic to see its target, expected evidence, score, and graph rationale.</p>';
    return;
  }
  const components = Object.entries(latest.score_components || {}).map(([name, value]) => `<div class="score-item"><span>${escape(name.replaceAll("_", " "))}</span><strong>${escape(value)}</strong></div>`).join("");
  const facts = (latest.graph_facts_used || []).map((fact) => `<li>${escape(fact)}</li>`).join("");
  $("#selection").innerHTML = `<div class="selection-callout"><strong>${escape(latest.tool_id)} → ${escape(latest.target_service_id)}</strong><span class="state-pill neutral">score ${escape(latest.score)}</span><p class="muted">Expected evidence: ${escape(latest.expected_evidence)} · ${escape(latest.risk_tier)} risk</p></div><ul class="reason-list">${(latest.reason_codes || []).map((reason) => `<li>${escape(reason.replaceAll("_", " "))}</li>`).join("")}</ul><p class="muted">Transition prior: ${escape(latest.historical_success_source || "not recorded")} (${escape(latest.score_components?.historical_success ?? "n/a")}).</p><h3>Score components</h3><div class="score-grid">${components}</div><h3>Graph facts considered</h3><ul class="fact-list">${facts || "<li>No dependency facts supplied.</li>"}</ul>`;
}

function observationLink(id) {
  return `<button type="button" class="evidence-link" data-observation="${escape(id)}">${escape(id)}</button>`;
}

function renderHypotheses(incident) {
  const hypotheses = incident.hypotheses || [];
  $("#hypotheses").innerHTML = hypotheses.length ? hypotheses.map((hypothesis) => {
    const support = (hypothesis.support_ids || []).map(observationLink).join(", ") || "none";
    const contradictions = (hypothesis.contradiction_ids || []).map(observationLink).join(", ") || "none";
    const missing = (hypothesis.missing_evidence || []).map((item) => `<li>${escape(item)}</li>`).join("");
    return `<article class="hypothesis ${escape(hypothesis.status)}"><span class="state-pill ${hypothesis.status === "leading" ? "safe" : hypothesis.status === "weakened" ? "blocked" : "warning"}">${escape(hypothesis.status)} · ${escape(hypothesis.confidence_label)} confidence</span><p><strong>${escape(hypothesis.statement)}</strong></p><p class="muted">${escape(hypothesis.explanation)}</p><small>Supporting observations: ${support}</small><small>Counter-evidence: ${contradictions}</small>${missing ? `<small>Unresolved checks:<ul>${missing}</ul></small>` : ""}</article>`;
  }).join("") : '<p class="empty-state">Hypotheses appear after the first observation.</p>';
}

function eventKind(type) {
  if (["tool_selected", "tool_completed"].includes(type)) return "tool";
  if (type === "hypotheses_evaluated") return "hypothesis";
  return "decision";
}

function timelineEvent(event) {
  const payload = event.payload || {};
  const type = event.event_type;
  let detail = "";
  if (type === "tool_selected") {
    detail = `<p><strong>${escape(payload.tool_id)}</strong> → ${escape(payload.target_service_id)} · score ${escape(payload.score)}</p><ul>${(payload.reason_codes || []).map((item) => `<li>${escape(item.replaceAll("_", " "))}</li>`).join("")}</ul>`;
  } else if (type === "tool_completed") {
    const observations = payload.observations || [];
    detail = observations.map((observation) => `<p><strong>${escape(observation.kind)}</strong> on ${escape(observation.service_id)}: ${escape(observation.summary)}<br><span class="muted">${escape(observation.source)} · ${escape(observation.timestamp)}</span></p>`).join("") || `<p>${escape(payload.status || "Tool completed without observations")}</p>`;
  } else if (type === "hypotheses_evaluated") {
    const leading = (payload.hypotheses || []).find((item) => item.status === "leading");
    detail = leading ? `<p>${escape(leading.statement)} · ${escape(leading.confidence_label)} confidence</p><p>Support: ${(leading.support_ids || []).map(observationLink).join(", ") || "none"} · Counter-evidence: ${(leading.contradiction_ids || []).map(observationLink).join(", ") || "none"}</p>` : "<p>No hypothesis update.</p>";
  } else if (type === "action_plan_proposed") {
    const plan = payload.action_plan || {};
    detail = `<p><strong>${escape(plan.action_type)} → ${escape(plan.target_service_id)}</strong> · ${escape(plan.status)}</p><p>Diagnosis ${escape(plan.hypothesis_id || "unresolved")} · evidence ${(plan.supporting_observation_ids || []).map(observationLink).join(", ") || "none"}</p>`;
  } else if (type === "action_plan_approved") {
    detail = `<p>Human approval recorded for plan ${escape(payload.plan_id)}. Simulation remains separate from real remediation.</p>`;
  } else if (type === "recovery_simulated") {
    const result = payload.verification || {};
    detail = `<p><strong>Simulation only.</strong> ${escape(result.summary || "Synthetic recovery verification recorded.")}</p>`;
  } else {
    detail = `<p>${escape(JSON.stringify(payload))}</p>`;
  }
  return `<li data-event-kind="${eventKind(type)}" ${payload.observations?.map((item) => `data-observation-${escape(item.id)}="true"`).join(" ") || ""}><span class="event-kind">${escape(type.replaceAll("_", " "))}</span><time datetime="${escape(event.created_at)}">${escape(new Date(event.created_at).toLocaleString())}</time><h3>${escape(type === "tool_selected" ? "Tool selected" : type === "tool_completed" ? "Observation recorded" : type === "hypotheses_evaluated" ? "Hypothesis update" : type === "action_plan_proposed" ? "Recovery proposal" : type === "action_plan_approved" ? "Approval recorded" : type === "recovery_simulated" ? "Simulated verification" : type.replaceAll("_", " "))}</h3>${detail}</li>`;
}

function renderTimeline(incident) {
  const events = (incident.trace || []).filter((event) => ["tool_selected", "tool_completed", "hypotheses_evaluated", "action_plan_proposed", "action_plan_approved", "recovery_simulated", "investigation_abstained", "evidence_validation_failed"].includes(event.event_type));
  $("#timeline").innerHTML = events.length ? events.map(timelineEvent).join("") : '<li class="empty-state">Run a diagnostic to build the evidence chain.</li>';
  filterTimeline();
}

function filterTimeline() {
  const selected = $("#timeline-filter").value;
  $("#timeline").querySelectorAll("li[data-event-kind]").forEach((item) => {
    item.classList.toggle("hidden", selected !== "all" && item.dataset.eventKind !== selected);
  });
}

function renderPlan(plan) {
  if (!plan) {
    $("#plan").innerHTML = '<p class="empty-state">A proposal is created only after evidence validation and impact checks.</p>';
    updateControls();
    return;
  }
  const impacts = plan.blast_radius?.map((item) => `${escape(item.service_name)} (${escape(item.team)})`).join(", ") || "No known dependent services";
  const checks = (plan.preconditions || []).map((item) => `<li class="${item.passed ? "safe" : "blocked"}">${item.passed ? "PASS" : "BLOCKED"} · ${escape(item.name)}</li>`).join("");
  const button = plan.status === "pending_approval"
    ? '<button class="button primary action" data-action="approve">Approve simulation</button>'
    : plan.status === "approved"
      ? '<button class="button primary action" data-action="simulate">Run simulation</button>'
      : "";
  $("#plan").innerHTML = `<span class="state-pill ${plan.status === "blocked" ? "blocked" : plan.status === "pending_approval" ? "warning" : "safe"}">${escape(plan.status.replaceAll("_", " "))}</span><dl class="plan-summary"><dt>Proposed action</dt><dd>${escape(plan.action_type)}</dd><dt>Target</dt><dd>${escape(plan.target_service_id)}</dd><dt>Diagnosis</dt><dd>${escape(plan.hypothesis_id || "No supported root-cause hypothesis")}</dd><dt>Evidence IDs</dt><dd>${(plan.supporting_observation_ids || []).map(observationLink).join(", ") || "none"}</dd><dt>Blast radius</dt><dd>${impacts}</dd><dt>Inventory</dt><dd>${plan.impact_complete ? "Complete" : "Incomplete — fail closed"}</dd><dt>Target validation</dt><dd>${(plan.preconditions || []).find((item) => item.name === "action target matches diagnostic evidence")?.passed ? "Evidence matched" : "Target/evidence mismatch"}</dd></dl><h3>Preconditions</h3><ul class="checks">${checks || "<li>No checks recorded.</li>"}</ul>${plan.blocked_reason ? `<p class="blocked">${escape(plan.blocked_reason)}</p>` : ""}${button}<p class="muted">Approval is required before a fixture-only simulation. No real infrastructure is touched.</p>`;
  updateControls();
}

function renderBenchmark(result) {
  const rows = (result.catalog_scale || []).map((item) => `<tr><td>${escape(item.catalog_size)}</td><td>${escape(item.case_count)}</td><td>${escape(item.graphroute.top1_accuracy_percent)}%</td><td>${escape(item.baseline.top1_accuracy_percent)}%</td><td>${escape(item.graphroute.mean_candidate_evaluations)}</td><td>${escape(item.graphroute.mean_selection_us)}</td><td>${escape(item.baseline.mean_selection_us)}</td><td>${escape(item.graphroute.correct_calls_within_budget_percent)}%</td><td><details><summary>Cases</summary><ul>${item.tasks.map((task) => `<li>${escape(task.task_id)}: expected ${escape(task.expected_tool)}; GraphRoute ${escape(task.graphroute_tool)} (${task.graphroute_correct ? "correct" : "incorrect"}); baseline ${escape(task.baseline_tool)} (${task.baseline_correct ? "correct" : "incorrect"})</li>`).join("")}</ul></details></td></tr>`).join("");
  $("#benchmark-data").innerHTML = `<p class="muted">${escape(result.measurement_note)}</p><div class="benchmark-table-wrap"><table class="benchmark-table"><thead><tr><th>Catalog size</th><th>Cases</th><th>GraphRoute accuracy</th><th>Flat baseline</th><th>Mean candidates</th><th>GraphRoute µs</th><th>Baseline µs</th><th>Within call budget</th><th>Per-task outcomes</th></tr></thead><tbody>${rows}</tbody></table></div><div class="benchmark-summary"><div class="summary-metric"><span>Task set</span><strong>${escape(result.task_count)} synthetic cases</strong></div><div class="summary-metric"><span>Call budget / method</span><strong>${escape(result.call_budget)} calls</strong></div><div class="summary-metric"><span>Scoring</span><strong>${escape(result.scoring_description)}</strong></div><div class="summary-metric"><span>Scope</span><strong>Synthetic evaluation only</strong></div></div><details><summary>Method and limitations</summary><p class="muted">${escape(result.methodology)}</p></details>`;
  $("#benchmark-output").classList.remove("hidden");
}

async function loadIncident(id) {
  const incident = await request(`/api/incidents/${encodeURIComponent(id)}`);
  render(incident);
  return incident;
}

async function start() {
  clearIncident("Starting a new investigation…");
  const button = $("#start");
  await perform(button, "Creating incident…", async () => {
    const incident = await request("/api/incidents", { method: "POST", body: JSON.stringify({ scenario_id: $("#scenario").value }) });
    return incident;
  }, "Incident created. Run a diagnostic to begin the evidence chain.");
}

async function step() {
  const incident = state.incident;
  if (!incident) return;
  await perform($("#step"), "Running diagnostic…", () => request(`/api/incidents/${encodeURIComponent(incident.id)}/step`, { method: "POST" }), "Diagnostic recorded; the incident view now reflects the API state.");
}

async function run() {
  const incident = state.incident;
  if (!incident) return;
  await perform($("#run"), "Running bounded investigation…", () => request(`/api/incidents/${encodeURIComponent(incident.id)}/run`, { method: "POST" }), "The bounded investigation stopped at its decision. Nothing was simulated.");
}

async function planAction(action) {
  const plan = state.incident?.action_plan;
  if (!plan || state.busy) return;
  const button = $(`#plan [data-action="${action}"]`);
  const expectedStatus = action === "approve" ? "pending_approval" : "approved";
  if (plan.status !== expectedStatus) {
    announce(`Cannot ${action}: the recovery proposal is ${plan.status}.`, true);
    return;
  }
  const path = `/api/action-plans/${encodeURIComponent(plan.id)}/${action === "approve" ? "approve" : "simulate"}`;
  await perform(button, action === "approve" ? "Recording approval…" : "Running simulation…", () => request(path, { method: "POST" }), action === "approve" ? "Approval recorded. The next action remains simulation-only." : "Simulation complete. No external system was contacted or changed.");
}

async function runBenchmark() {
  const button = $("#benchmark");
  await perform(button, "Evaluating catalog sizes…", async () => {
    const result = await request("/api/benchmark", { method: "POST" });
    renderBenchmark(result);
    return { noRender: true };
  }, "Synthetic catalog-scale evaluation complete.");
}

function exportTrace() {
  if (!state.incident) return;
  const data = JSON.stringify(state.incident.trace || [], null, 2);
  const url = URL.createObjectURL(new Blob([data], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `${state.incident.id}-trace.json`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  announce("Investigation trace exported as JSON.");
}

function handleEvidenceClick(event) {
  const button = event.target.closest("[data-observation]");
  if (!button) return;
  const observationId = button.dataset.observation;
  const item = $("#timeline").querySelector(`[data-observation-${CSS.escape(observationId)}="true"]`);
  if (item) {
    $("#timeline-filter").value = "all";
    filterTimeline();
    item.scrollIntoView({ behavior: "smooth", block: "center" });
    item.setAttribute("tabindex", "-1");
    item.focus({ preventScroll: true });
  } else {
    announce(`Observation ${observationId} is in the incident ledger but has no separate timeline event.`);
  }
}

async function initialize() {
  if (window.location.protocol === "file:") {
    renderHealth({ graph: { mode: "fixture", connected: true, detail: "Static file preview — API status is not available." } });
    $("#health").textContent = "Static preview · API unavailable";
    announce("This is a static preview. Start GraphOps using .\\scripts\\start_demo.ps1, then open http://127.0.0.1:8000. A local file cannot call the API.");
    return;
  }
  try {
    const [health, scenarios] = await Promise.all([request("/health"), request("/api/scenarios")]);
    renderHealth(health);
    state.scenarios = scenarios;
    $("#scenario").innerHTML = scenarios.map((item) => `<option value="${escape(item.id)}">${escape(item.title)} · ${escape(item.severity)}</option>`).join("");
    updateScenarioSummary();
    clearIncident("Choose a synthetic scenario to begin. No external infrastructure is changed by this demo.");
    window.setInterval(async () => {
      try { renderHealth(await request("/health")); }
      catch { renderHealth(null); }
    }, 30000);
  } catch (error) {
    $("#health").textContent = "Error · API unavailable";
    $("#health").className = "badge blocked";
    announce(`GraphOps could not load its health or scenario data: ${error.message}`, true);
  }
  $("#start").addEventListener("click", start);
  $("#step").addEventListener("click", step);
  $("#run").addEventListener("click", run);
  $("#benchmark").addEventListener("click", runBenchmark);
  $("#scenario").addEventListener("change", () => {
    clearIncident("Scenario changed. The previous incident, evidence, plan, graph, and trace were cleared.");
    updateScenarioSummary();
  });
  $("#timeline-filter").addEventListener("change", filterTimeline);
  $("#plan").addEventListener("click", (event) => {
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action) planAction(action);
  });
  $("#hypotheses").addEventListener("click", handleEvidenceClick);
  $("#plan").addEventListener("click", handleEvidenceClick);
  $("#timeline").addEventListener("click", handleEvidenceClick);
  $("#export-trace").addEventListener("click", exportTrace);
  $("#graph").addEventListener("click", (event) => {
    const target = event.target.closest("[data-graph-node], [data-graph-edge]");
    if (target) describeGraphSelection(target);
  });
  $("#graph").addEventListener("keydown", (event) => {
    const target = event.target.closest("[data-graph-node], [data-graph-edge]");
    if (target && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      describeGraphSelection(target);
    }
  });
  $("#graph-text").addEventListener("click", (event) => {
    const target = event.target.closest("[data-graph-node], [data-graph-edge]");
    if (target) describeGraphSelection(target);
  });
}

initialize();
