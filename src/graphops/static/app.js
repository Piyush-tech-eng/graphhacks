const state = { incident: null };
const $ = (selector) => document.querySelector(selector);
const escape = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" })[char]);

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const isJson = response.headers.get("content-type")?.includes("application/json");
  const body = isJson ? await response.json() : {};
  if (!response.ok) throw new Error(body.detail || "Request failed");
  return body;
}

function setNotice(message) { $("#notice").textContent = message; }
function statusClass(status = "") { return status.includes("blocked") ? "blocked" : status.includes("approval") || status.includes("pending") ? "warning" : "safe"; }

function render(data) {
  const incident = data.state || data;
  state.incident = incident;
  $("#workspace").classList.remove("hidden");
  $("#step").disabled = Boolean(incident.action_plan);
  $("#run").disabled = Boolean(incident.action_plan);
  const context = incident.graph_context || {};
  $("#incident").innerHTML = `<dl class="kv"><dt>Severity</dt><dd>${escape(incident.severity)}</dd><dt>Status</dt><dd><span class="status ${statusClass(incident.status)}">${escape(incident.status)}</span></dd><dt>Summary</dt><dd>${escape(incident.summary)}</dd><dt>Tenant</dt><dd>${escape(incident.tenant_id)} — structurally isolated graph</dd></dl>`;
  const paths = (context.dependency_paths || []).map((path) => `<span class="chip">${path.map(escape).join(" → ")}</span>`).join("") || "<span class='chip'>No dependency path recorded</span>";
  $("#graph").innerHTML = `<div class="chips">${paths}</div><p class="subtitle">Graph mode: ${escape(context.graph_mode?.mode || "fixture")}. The graph changes which target is inspected next.</p>`;
  $("#hypotheses").innerHTML = (incident.hypotheses || []).map((h) => `<div class="hypothesis ${escape(h.status)}"><span class="status ${statusClass(h.status)}">${escape(h.status)} · ${escape(h.confidence_label)}</span><br>${escape(h.statement)}<small>Support: ${h.support_ids?.map(escape).join(", ") || "none"} · Counter-evidence: ${h.contradiction_ids?.map(escape).join(", ") || "none"}</small></div>`).join("") || "<p class='subtitle'>Evidence ledger will appear after the first tool result.</p>";
  const timeline = (incident.trace || []).filter((event) => ["tool_selected", "tool_completed", "action_plan_proposed", "action_plan_approved", "recovery_simulated"].includes(event.event_type));
  $("#timeline").innerHTML = timeline.map((event) => `<li><strong>${escape(event.event_type.replaceAll("_", " "))}</strong><small>${escape(JSON.stringify(event.payload))}</small></li>`).join("") || "<li>No steps recorded.</li>";
  renderPlan(incident.action_plan);
  $("#trace").textContent = JSON.stringify(incident.trace || [], null, 2);
}

function renderPlan(plan) {
  if (!plan) { $("#plan").innerHTML = "<p class='subtitle'>A plan is created only after evidence validation. It cannot execute anything outside the simulator.</p>"; return; }
  const impacts = plan.blast_radius?.map((item) => `${escape(item.service_name)} (${escape(item.team)})`).join(", ") || "No known dependents";
  let actions = "";
  if (plan.status === "pending_approval") actions = `<button id="approve" data-plan="${escape(plan.id)}">Approve simulation</button>`;
  if (plan.status === "approved") actions = `<button id="simulate" data-plan="${escape(plan.id)}">Run simulation only</button>`;
  $("#plan").innerHTML = `<p><span class="status ${statusClass(plan.status)}">${escape(plan.status)}</span></p><dl class="kv"><dt>Action</dt><dd>${escape(plan.action_type)} on ${escape(plan.target_service_id)}</dd><dt>Impact</dt><dd>${impacts}</dd><dt>Inventory</dt><dd>${plan.impact_complete ? "complete" : "unknown — fail closed"}</dd><dt>Reason</dt><dd>${escape(plan.reason)}</dd></dl><p class="subtitle">${escape(plan.blocked_reason || "Explicit approval is required before a fixture-only simulation.")}</p>${actions}`;
  $("#approve")?.addEventListener("click", () => approve(plan.id));
  $("#simulate")?.addEventListener("click", () => simulate(plan.id));
}

async function refresh() { if (state.incident) render(await request(`/api/incidents/${state.incident.id}`)); }
async function start() { try { const incident = await request("/api/incidents", { method:"POST", body: JSON.stringify({ scenario_id: $("#scenario").value }) }); render(incident); setNotice("Incident created. Run each safe step, or let the bounded loop proceed to a plan."); } catch (error) { setNotice(error.message); } }
async function step() { try { render(await request(`/api/incidents/${state.incident.id}/step`, { method:"POST" })); setNotice("Recorded one safe investigation step and updated the evidence ledger."); } catch (error) { setNotice(error.message); } }
async function run() { try { render(await request(`/api/incidents/${state.incident.id}/run`, { method:"POST" })); setNotice("The bounded loop stopped at the recovery decision; nothing has been simulated."); } catch (error) { setNotice(error.message); } }
async function approve(planId) { try { render(await request(`/api/action-plans/${planId}/approve`, { method:"POST" })); setNotice("Approval recorded. The next action is still simulation only."); } catch (error) { setNotice(error.message); } }
async function simulate(planId) { try { render(await request(`/api/action-plans/${planId}/simulate`, { method:"POST" })); setNotice("Simulation complete. No external system was contacted or changed."); } catch (error) { setNotice(error.message); } }
async function benchmark() { try { const result = await request("/api/benchmark", { method:"POST" }); $("#benchmark-output").classList.remove("hidden"); $("#benchmark-data").innerHTML = `<p class="subtitle">${escape(result.measurement_note)}</p><table><thead><tr><th>Scenario</th><th>GraphOps</th><th>Flat baseline</th><th>Action decision</th></tr></thead><tbody>${result.cases.map((item) => `<tr><td>${escape(item.scenario_id)}</td><td>${item.graphops_correct ? "✓" : "×"} ${escape(item.graphops_hypothesis)}</td><td>${item.flat_baseline_correct ? "✓" : "×"} ${escape(item.flat_baseline_hypothesis)}</td><td>${escape(item.action_status)}</td></tr>`).join("")}</tbody></table><p><strong>Fixture Top-1 accuracy:</strong> GraphOps ${Math.round(result.summary.graphops_top1_accuracy * 100)}% · Flat ${Math.round(result.summary.flat_baseline_top1_accuracy * 100)}%</p>`; } catch (error) { setNotice(error.message); } }

async function init() {
  if (window.location.protocol === "file:") {
    $("#health").textContent = "static preview · API unavailable";
    setNotice("This is a static preview. Start GraphOps with .\\scripts\\start_demo.ps1, then open http://127.0.0.1:8000. A local file cannot call the API by itself.");
    ["#start", "#step", "#run", "#benchmark"].forEach((selector) => { $(selector).disabled = true; });
    return;
  }
  try {
    const [health, scenarios] = await Promise.all([request("/health"), request("/api/scenarios")]);
    $("#health").textContent = `${health.graph.mode} graph · ${health.graph.connected ? "connected" : "fallback"}`;
    $("#scenario").innerHTML = scenarios.map((item) => `<option value="${escape(item.id)}">${escape(item.title)} · ${escape(item.severity)}</option>`).join("");
  } catch (error) { setNotice(error.message); }
  $("#start").addEventListener("click", start); $("#step").addEventListener("click", step); $("#run").addEventListener("click", run); $("#benchmark").addEventListener("click", benchmark);
}
init();
