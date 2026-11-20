/* Connection to the sourcing API. The local demo remains available offline. */
(() => {
  const base = (document.querySelector('meta[name="founderhunt-api"]')?.content || "").replace(/\/$/, "");
  const sectors = {
    industrial_climate_deeptech: "Industrial & Deep Tech",
    digital_tech_ai: "Digital Tech",
    life_sciences_chemistry: "Life Sciences"
  };
  const array = value => Array.isArray(value) ? value : [];
  const text = value => typeof value === "string" ? value.trim() : "";

  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch { return ""; }
  }

  function dateOnly(value) {
    const date = text(value).slice(0, 10);
    const parsed = new Date(date);
    return /^\d{4}-\d{2}-\d{2}$/.test(date) && Number.isFinite(+parsed) &&
      parsed.toISOString().slice(0, 10) === date ? date : "";
  }

  let operatorToken = "";
  async function request(path, body) {
    const isPost = body !== undefined;
    const response = await fetch(base + "/api/" + path, {
      credentials: "omit",
      method: isPost ? "POST" : "GET",
      body: isPost ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(isPost ? 90000 : 4500),
      headers: { Accept: "application/json", ...(isPost ? {
        "Content-Type": "application/json",
      } : {}), ...(operatorToken && (isPost || path === "workspace/saved") ? { Authorization: "Bearer " + operatorToken } : {}) }
    });
    if (!response.ok) {
      const error = await response.json().catch(() => null);
      throw new Error(typeof error?.detail === "string" ? error.detail : "Sourcing API returned " + response.status);
    }
    return response.json();
  }

  async function load() {
    const demo = { ...window.SCOUT_DATA, people: window.SCOUT_PEOPLE };
    if (!base && !["http:", "https:"].includes(location.protocol)) return demo;
    try {
      const response = await request("leads");
      const teams = new Map();
      const sources = [];
      const people = [];
      for (const lead of array(response.leads)) {
        const entityId = text(lead?.entity_id);
        const name = text(lead?.name) || text(lead?.canonical_name);
        if (!name || !/^[\w:-]+$/.test(entityId) || teams.has(entityId)) continue;

        const id = "api:" + entityId;
        const focus = text(lead.description);
        const website = safeUrl(lead.website_url);
        const signals = array(lead.discovered_from).filter(signal => signal && typeof signal === "object").map((signal, index) => {
          const sourceId = id + ":signal:" + index;
          sources.push({
            id: sourceId, publisher: text(signal.source_label) || text(signal.source_name) || "Public source",
            url: safeUrl(signal.source_url), event_on: dateOnly(signal.signal_date), stage: "source", backend: true
          });
          return { source_id: sourceId, team_name: name, focus, evidence: text(signal.summary) };
        });
        teams.set(entityId, {
          id, name, focus, summary: focus, city: text(lead.city), sector: sectors[lead.htgf_vertical] || "Unclassified",
          signals, entityId, website, whyNow: text(lead.inflection_why_now),
          discovery: dateOnly(lead.discovered_at || lead.first_seen_at),
          funding: text(lead.funding_summary),
          evidence: array(lead.score_breakdown?.evidence_urls).map(safeUrl).filter(Boolean),
          drivers: array(lead.score_breakdown?.drivers).map(text).filter(Boolean)
        });

        const founders = array(lead.founders).filter(founder => text(founder?.full_name) &&
          !text(founder.full_name).startsWith("Lead PI ("));
        const founder = founders.find(person => /\bceo\b/i.test(text(person.role))) || founders[0];
        if (founder) {
          const linkedin = safeUrl(founder.linkedin_url);
          const source = website || signals.map(signal => sources.find(item => item.id === signal.source_id)?.url).find(Boolean);
          people.push({
            id: id + ":founder", name: text(founder.full_name), role: text(founder.role) || "Founder",
            contribution: array(founder.universities).map(text).filter(Boolean).join(" · "),
            projects: [id], source, ventures: [], profiles: {
              linkedin: linkedin && /(^|\.)linkedin\.com$/i.test(new URL(linkedin).hostname) ? linkedin : "",
              github: /^[a-z0-9](?:[a-z0-9-]{0,38})$/i.test(text(founder.github_handle))
                ? "https://github.com/" + founder.github_handle : "",
              more: []
            }
          });
        }
      }
      return { connected: true, teams: [...teams.values(), ...demo.teams], sources: [...sources, ...demo.sources],
        people: [...demo.people, ...people], liveCount: teams.size };
    } catch {
      return demo;
    }
  }

  window.FOUNDERHUNT_API = {
    load,
    memo: id => request("leads/" + encodeURIComponent(id)),
    github: username => request("founders/github/" + encodeURIComponent(username)),
    dashboard: () => request("dashboard"),
    events: () => request("events"),
    health: () => request("health"),
    savedWorkspace: () => request("workspace/saved"),
    saveWorkspace: preferences => request("workspace/saved", preferences),
    enrich: target => request("enrich/live", { domain_or_name: target }),
    dispatch: id => request("dispatch/n8n/" + encodeURIComponent(id), {}),
    setOperatorToken: token => { operatorToken = token.trim(); }
  };
})();
