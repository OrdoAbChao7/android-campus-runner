(() => {
  const $ = (id) => document.getElementById(id);
  let intentId = "";
  const headers = () => ({ "Content-Type": "application/json", "X-Local-Control-Token": $("token").value });
  const show = (text, error = false) => { $("message").textContent = text; $("message").style.color = error ? "#b3261e" : ""; };
  async function json(url, options) { const response = await fetch(url, options); const data = await response.json(); if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`); return data; }
  async function load() {
    try {
      const [accounts, routes, status] = await Promise.all([json("/api/accounts"), json("/api/routes"), json("/api/run/status")]);
      $("enterprise").replaceChildren(...accounts.accounts.map(a => new Option(a.enterprise, a.enterprise)));
      $("route").replaceChildren(...routes.routes.map(r => new Option(r, r)));
      render(status);
    } catch (e) { show(e.message, true); }
  }
  function render(s) {
    $("badge").textContent = s.running ? `运行中 · ${s.current || "准备中"}` : "空闲";
    $("current").textContent = s.current || "—"; $("completed").textContent = (s.completed || []).join("、") || "—"; $("failed").textContent = (s.failed || []).join("、") || "—";
    $("logs").textContent = (s.log_lines || []).join("\n"); $("stop").disabled = !s.running;
  }
  $("confirm").addEventListener("change", () => { $("authorize").disabled = !$("confirm").checked; });
  $("authorize").addEventListener("click", async () => {
    try { const d = await json("/api/run/authorize", { method: "POST", headers: headers(), body: JSON.stringify({ confirm: "START_CAMPUS_RUN", serial: $("serial").value.trim(), enterprise: $("enterprise").value, route: $("route").value }) }); intentId = d.intent_id; $("start").disabled = false; show(`授权已采集：${intentId}`); } catch (e) { show(e.message, true); }
  });
  $("start").addEventListener("click", async () => { try { const d = await json("/api/run/start", { method: "POST", headers: headers(), body: JSON.stringify({ intent_id: intentId }) }); $("start").disabled = true; show(d.message); } catch (e) { show(e.message, true); } });
  $("stop").addEventListener("click", async () => { try { const d = await json("/api/run/stop", { method: "POST", headers: headers(), body: "{}" }); show(d.message); } catch (e) { show(e.message, true); } });
  setInterval(async () => { try { render(await json("/api/run/status")); } catch (_) {} }, 1500); load();
})();
