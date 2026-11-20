/* FounderHunt. The UI consumes the browser-safe roster in roster.js. */
(async () => {
  "use strict";

  const logo = document.querySelector(".wordmark img");
  logo.addEventListener("load", () => logo.parentElement.classList.add("has-logo"), { once: true });
  if (logo.naturalWidth) logo.parentElement.classList.add("has-logo");

  const { teams, sources, people, liveCount, connected } = await window.FOUNDERHUNT_API.load();
  if (liveCount) document.getElementById("dataMode").textContent = "Backend + demo";
  document.getElementById("sourcingNav").hidden = !connected;
  const recommendations = window.SCOUT_RECOMMENDATIONS;
  const teamById = new Map(teams.map(team => [team.id, team]));
  const currentVentures = new Map();
  for (const person of people) {
    for (const id of person.projects) {
      const team = teamById.get(id);
      currentVentures.set(id, { ...team, lead: person.name });
    }
  }
  const otherVentures = people.flatMap(person => person.ventures.map(venture => ({ ...venture, lead: person.name })));
  const ventures = [...currentVentures.values(), ...otherVentures];
  const trackedById = new Map([...people, ...ventures].map(item => [item.id, item]));
  const ventureIds = new Set(ventures.map(venture => venture.id));
  const sourceById = new Map(sources.map(source => [source.id, source]));
  const actions = ["shortlist", "watch", "pass"];
  const actionLabels = { shortlist: "Shortlist", watch: "Watch", pass: "Pass" };
  const actionIcons = { shortlist: "bookmark", watch: "clock", pass: "cross" };
  const collectionLabels = { shortlist: "Shortlisted", watch: "Watching", pass: "Passed" };
  const rosterLabels = {
    "baystartup-phase1": "BayStartUP · Phase 1",
    "baystartup-final": "BayStartUP · Finalists",
    "tum-final": "TUM IDEAward · Finalists"
  };
  const sectorClasses = {
    "Digital Tech": "digital", "Life Sciences": "life", "Industrial & Deep Tech": "deep"
  };
  const storageKey = "first-signal-review-v1";
  const byId = id => document.getElementById(id);
  const query = byId("query");
  const sector = byId("sector");
  const sort = byId("sort");
  const dialog = byId("leadDialog");
  const deckArea = byId("deckArea");
  const toast = byId("toast");
  let page = "explore";
  let kind = "projects";
  let preferredDeckId = null;
  let detailTrigger = null;
  let ranking = null;
  let collectionTab = "tracked";
  let detailId = null;
  let currentDeckId = null;
  let toastTimer;
  let preferences = loadPreferences();

  function escape(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
  }

  function icon(name) {
    return '<svg class="icon" aria-hidden="true" width="18" height="18"><use href="#i-' + escape(name) + '"></use></svg>';
  }

  function normalize(value) {
    return String(value || "").toLowerCase().normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "").replace(/\bmuenchen\b|\bmunchen\b/g, "munich");
  }

  const stopWords = new Set("a an and are at based building companies company for from i in is me on or show startups startup team teams technology the to what which with working find search".split(" "));
  function queryTerms() {
    return [...new Set((normalize(query.value).match(/[a-z0-9]+/g) || []).filter(term => !stopWords.has(term)))];
  }

  function cityName(city) {
    return String(city || "").replace(/München/g, "Munich").replace(/ bei Munich/g, " near Munich");
  }

  function readEmpty() {
    return { decisions: {}, tracked: {}, history: [] };
  }

  function knownOrBackend(records, id) {
    return records.has(id) || (typeof id === "string" && id.startsWith("api:"));
  }

  function availableUndoIndex() {
    return preferences.history.findLastIndex(change =>
      (change.kind === "person" ? trackedById : teamById).has(change.id));
  }

  function loadPreferences() {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey));
      if (!saved || typeof saved.decisions !== "object" || !Array.isArray(saved.history)) return readEmpty();
      const decisions = Object.fromEntries(Object.entries(saved.decisions).filter(([id, choice]) => knownOrBackend(teamById, id) && actions.includes(choice)));
      const tracked = Object.fromEntries(Object.entries(saved.tracked || {}).filter(
        ([id, choice]) => knownOrBackend(trackedById, id) && choice === "track"
      ));
      const history = saved.history.filter(change => {
        if (!change || change.kind === "roster") return false;
        const person = change.kind === "person";
        const choices = person ? ["track", null] : [...actions, null];
        return knownOrBackend(person ? trackedById : teamById, change.id) &&
          choices.includes(change.before) && choices.includes(change.after);
      });
      return { ...saved, decisions, tracked, history };
    } catch {
      return readEmpty();
    }
  }

  function writePreferences() {
    try {
      localStorage.setItem(storageKey, JSON.stringify(preferences));
      return true;
    } catch {
      return false;
    }
  }

  function showToast(message, canUndo = false) {
    if (!toast) return;
    clearTimeout(toastTimer);
    toast.innerHTML = '<span>' + escape(message) + '</span>' + (canUndo ? ' <button type="button" data-undo>Undo</button>' : '');
    toast.classList.add("is-visible");
    toastTimer = setTimeout(() => toast.classList.remove("is-visible"), 3600);
  }

  function makeOptions(element, firstLabel, choices) {
    const all = new Option(firstLabel, "all");
    element.replaceChildren(all, ...choices.map(([value, label]) => new Option(label, value)));
  }

  makeOptions(sector, "All sectors", [...new Set(teams.map(team => team.sector))].sort().map(value => [value, value]));

  // Keep search and sorting separate so an API can supply ranked records later.
  const index = teams.map((team, order) => {
    const names = normalize([team.name, ...team.signals.map(signal => signal.team_name),
      ...people.filter(person => person.projects.includes(team.id)).map(person => person.name)].join(" "));
    const topics = normalize([team.focus, team.sector, cityName(team.city)].join(" "));
    const body = normalize([team.summary, ...team.signals.map(signal => [signal.focus, signal.evidence].filter(Boolean).join(" "))].join(" "));
    const latestEvent = Math.max(0, ...team.signals.map(signal => Date.parse(sourceById.get(signal.source_id).event_on) || 0));
    return { team, order, names, topics, body, latestEvent };
  });

  function rankedTeams() {
    const requestKey = JSON.stringify([query.value, sector.value, "all"]);
    const model = ranking?.key === requestKey ? ranking : null;
    const terms = queryTerms();
    const matches = [];
    for (const record of index) {
      const team = record.team;
      if (sector.value !== "all" && team.sector !== sector.value) continue;
      const weights = terms.map(term =>
        record.names.includes(term) ? 7 : record.topics.includes(term) ? 4 : record.body.includes(term) ? 1 : 0
      );
      if (model ? !model.order.has(team.id) : weights.some(weight => !weight)) continue;
      matches.push({ record, relevance: weights.reduce((total, weight) => total + weight, 0) });
    }
    matches.sort((a, b) => {
      const left = a.record, right = b.record;
      if (sort.value === "name") return left.team.name.localeCompare(right.team.name, "en");
      if (sort.value === "recent") return right.latestEvent - left.latestEvent || right.team.signals.length - left.team.signals.length || left.order - right.order;
      if (sort.value.startsWith("discovery-")) {
        const direction = sort.value === "discovery-newest" ? -1 : 1;
        const leftDate = discoveryByTeam.get(left.team.id);
        const rightDate = discoveryByTeam.get(right.team.id);
        return Number(!leftDate) - Number(!rightDate) ||
          direction * leftDate.localeCompare(rightDate) ||
          left.team.name.localeCompare(right.team.name, "en");
      }
      if (model) return model.order.get(left.team.id) - model.order.get(right.team.id);
      if (a.relevance !== b.relevance) return b.relevance - a.relevance;
      if (left.team.entityId || right.team.entityId) {
        return Number(!left.team.entityId) - Number(!right.team.entityId) || left.order - right.order;
      }
      return right.team.signals.length - left.team.signals.length || left.order - right.order;
    });
    return matches.map(item => item.record.team);
  }

  function sectorTag(team) {
    return '<span class="sector-tag ' + (sectorClasses[team.sector] || 'digital') + '">' + escape(team.sector) + '</span>';
  }

  const motifs = {
    life: '<ellipse cx="30" cy="18" rx="25" ry="9"/><ellipse cx="30" cy="18" rx="25" ry="9" transform="rotate(60 30 18)"/><ellipse cx="30" cy="18" rx="25" ry="9" transform="rotate(120 30 18)"/>',
    deep: '<path d="M3 25 29 4l28 21M3 33 29 12l28 21M3 17 29-4l28 21"/>',
    digital: '<path d="M2 23h13l8-15 11 23 8-12h16M2 30h15l6-11 11 22 11-15h13"/>'
  };

  function signalCount(team) {
    return team.signals.length + ' public signal' + (team.signals.length === 1 ? '' : 's');
  }

  function location(team) {
    return team.city ? '<span>' + icon('pin') + ' ' + escape(cityName(team.city)) + '</span>' : '';
  }

  const today = new Date().toISOString().slice(0, 10);
  const milestoneLabels = {
    appearance: "Public showcase", funding: "Grant announced", register: "Register notice",
    post: "Public post", nominee: "Competition nominee", finalist: "Competition finalist", award: "Competition award",
    event: "Event listing", source: "Source signal"
  };

  function ventureTimeline(team) {
    const recorded = team.signals.map(signal => {
      const source = sourceById.get(signal.source_id);
      const type = source.stage || "event";
      return {
        date: source.published_on || source.event_on,
        published: Boolean(source.published_on),
        publicSignal: Boolean(source.published_on || (source.backend && source.event_on)),
        upcoming: !source.published_on && source.event_on > today,
        type,
        title: milestoneLabels[type],
        description: evidenceText(signal),
        source,
        eventDated: !source.backend && !source.published_on
      };
    });
    const additional = (window.SCOUT_MILESTONES[team.id] || []).map(milestone => ({
      ...milestone,
      published: milestone.type !== "appearance",
      publicSignal: true,
      source: { publisher: milestone.publisher, url: milestone.url }
    }));
    return [...recorded, ...additional].sort((a, b) => Number(!a.date) - Number(!b.date) || a.date.localeCompare(b.date));
  }

  const discoveryByTeam = new Map(teams.map(team => {
    if (team.entityId) return [team.id, team.discovery];
    const timeline = ventureTimeline(team);
    const earliest = timeline.find(entry => entry.publicSignal) || timeline[0];
    // Seed the demo offset with the venture ID so dates are consistent between sessions.
    let seed = 0;
    for (const char of team.id) seed = Math.imul(seed, 31) + char.charCodeAt(0) | 0;
    const date = new Date(earliest.date);
    date.setUTCDate(date.getUTCDate() + 1 + ((seed >>> 0) % 75));
    return [team.id, date.toISOString().slice(0, 10)];
  }));

  function discoveryDate(team) {
    const date = discoveryByTeam.get(team.id);
    return date ? timelineDate({ date }) : "Unverified";
  }

  function ventureHistory(team, timeline = ventureTimeline(team)) {
    const firstPublic = timeline.find(entry => entry.publicSignal && entry.date <= today);
    const first = firstPublic || timeline.find(entry => entry.date);
    const date = first?.date;
    const now = new Date(today);
    const start = new Date(date);
    const days = Math.round((now - start) / 86400000);
    const months = (now.getUTCFullYear() - start.getUTCFullYear()) * 12 +
      now.getUTCMonth() - start.getUTCMonth() - Number(now.getUTCDate() < start.getUTCDate());
    const years = Math.floor(months / 12);
    const elapsed = days < 0 ? "Upcoming" : days === 0 ? "Today" : months < 1
      ? days + (days === 1 ? " day" : " days")
      : [years ? years + "y" : "", months % 12 ? months % 12 + "mo" : ""].filter(Boolean).join(" ");
    const stage = first?.type || "unknown";
    const dateLabelText = firstPublic ? "Spottable by" : first?.upcoming ? "First scheduled event" : "Earliest dated event";
    const elapsedLabel = firstPublic ? "Since first signal" : first?.upcoming ? "Status" : "Since event";

    return '<dl class="venture-history"><div><dt>' + dateLabelText + '</dt><dd>' + (first ? timelineDate(first) : 'Unverified') +
      '</dd></div><div><dt>' + elapsedLabel + '</dt><dd>' + (first ? elapsed : 'Unverified') + '</dd></div>' +
      '<div><dt>First recorded stage</dt><dd>' + stageTag(stage) + '</dd></div>' +
      '<div class="venture-discovery"><dt>' + (team.entityId ? 'Discovered' : 'Discovered (demo)') + '</dt><dd>' + discoveryDate(team) + '</dd></div></dl>';
  }

  function timelineDate(entry) {
    if (!entry.date) return "Undated";
    return '<time datetime="' + escape(entry.date) + '">' + escape(entry.dateText || dateLabel(entry.date)) + '</time>';
  }

  function stageTag(stage) {
    return '<span class="venture-stage ' + escape(stage) + '">' +
      escape(milestoneLabels[stage] || "Unverified") + '</span>';
  }

  function timelineSection(timeline) {
    const firstPublic = timeline.find(entry => entry.publicSignal && entry.date <= today);
    const firstPost = timeline.find(entry => entry.published && entry.type !== "register" && entry.date <= today);
    const summary = '<dl class="timeline-summary"><div><dt>Earliest documented public signal</dt><dd>' +
      (firstPublic ? timelineDate(firstPublic) : 'Date unverified') +
      '</dd></div><div><dt>Earliest dated post</dt><dd>' + (firstPost ? timelineDate(firstPost) : 'Date unverified') +
      '</dd></div></dl>';
    const steps = timeline.map(entry =>
      '<li class="signal-item timeline-step' + (entry === firstPublic ? ' first-public' : '') + '">' +
      '<div class="timeline-meta">' + timelineDate(entry) + stageTag(entry.type) +
      (entry === firstPublic ? '<span class="timeline-first">Spottable signal</span>' : '') +
      (entry.upcoming ? '<span class="timeline-first">Upcoming</span>' : '') +
      (entry.eventDated && !entry.upcoming ? '<span class="timeline-first">Event date</span>' : '') + '</div>' +
      '<h4>' + escape(entry.title) + '</h4>' +
      (entry.description ? '<p>' + escape(entry.description) + '</p>' : '') + sourceLink(entry.source) + '</li>'
    ).join("");
    return '<section class="venture-timeline" aria-labelledby="timelineTitle"><h3 id="timelineTitle">Public timeline</h3>' +
      summary + '<p class="timeline-note">Dates reflect the available sources. A public signal marks when investors could have found the venture.</p>' +
      (steps ? '<ol class="signal-list">' + steps + '</ol>' : '<p>No source signals available.</p>') + '</section>';
  }

  function recommendationPanel(id, supportingPoint, evidence = "") {
    const recommendation = recommendations[id];
    if (!recommendation) return "";
    const [thesis, nextCheck] = recommendation;
    return '<details class="recommendation-panel recommendation-pros">' +
      '<summary><span>Why take a closer look?</span><span class="recommendation-toggle" aria-hidden="true"></span></summary>' +
      '<div class="recommendation-content"><div class="recommendation-header"><strong>Pros</strong>' +
      '<span class="recommendation-mode">Prototype</span></div>' +
      '<ul><li>' + escape(thesis) + '</li><li>' + escape(supportingPoint) + '</li></ul>' +
      '<div class="recommendation-next"><strong>Next check</strong><p>' + escape(nextCheck) + '</p></div>' +
      (evidence ? '<div class="recommendation-evidence"><strong>Evidence</strong>' + evidence + '</div>' : '') +
      '</div></details>';
  }

  function shortlistButton(team) {
    const saved = preferences.decisions[team.id] === "shortlist";
    const label = (saved ? "Remove " : "Add ") + team.name + (saved ? " from shortlist" : " to shortlist");
    return '<button type="button" class="save-button" data-save="' + escape(team.id) +
      '" aria-label="' + escape(label) + '" aria-pressed="' + saved + '">' + icon('bookmark') + '</button>';
  }

  function leadCard(team) {
    const tone = sectorClasses[team.sector] || 'digital';
    const signal = bestSignal(team);
    const founder = people.find(person => person.projects.includes(team.id));
    const founderCallout = founder
      ? '<div class="venture-founder">' + icon('mark') + '<div><span>Founder to watch</span><strong>' +
        escape(founder.name) + '</strong></div></div>'
      : '';
    return '<article class="lead-card ' + tone + '" data-lead="' + escape(team.id) + '">' +
      '<div class="card-top">' + sectorTag(team) + '<svg class="motif" viewBox="0 -8 60 52" aria-hidden="true">' + motifs[tone] + '</svg></div>' +
      '<h3><button type="button" class="lead-open" data-open="' + escape(team.id) + '">' + escape(team.name) + '</button></h3>' +
      '<p class="lead-summary">' + escape(team.summary) + '</p>' + founderCallout + ventureHistory(team) +
      '<div class="card-footer"><div class="card-meta">' + location(team) + '<span>' + signalCount(team) + '</span></div>' +
      shortlistButton(team) + '</div>' +
      (team.entityId ? backendBrief(team) : recommendationPanel(team.id, evidenceText(signal), sourceLink(sourceById.get(signal.source_id)))) + '</article>';
  }

  function matchingPeople() {
    const terms = queryTerms();
    const matches = people.flatMap(person => {
      const projects = person.projects.map(id => teamById.get(id))
        .filter(project => sector.value === "all" || project.sector === sector.value);
      if (!projects.length) return [];
      const name = normalize(person.name);
      const text = normalize([person.role, person.contribution, "team lead", ...person.ventures.map(venture => venture.name),
        ...projects.flatMap(project => [project.name, project.focus, project.sector, cityName(project.city)])].join(" "));
      const scores = terms.map(term => name.includes(term) ? 3 : text.includes(term) ? 1 : 0);
      return scores.includes(0) ? [] : [{ person, score: scores.reduce((sum, value) => sum + value, 0) }];
    });
    matches.sort((a, b) => sort.value === "name" ? a.person.name.localeCompare(b.person.name, "en") : b.score - a.score);
    return matches.map(match => match.person);
  }

  function trackButton(item) {
    const tracked = preferences.tracked[item.id] === "track";
    const follow = ventureIds.has(item.id);
    const action = follow ? "Follow" : "Track";
    return '<button type="button" class="outline track-button" data-track="' + escape(item.id) +
      '" aria-label="' + action + ' ' + escape(item.name) + '" aria-pressed="' + tracked + '">' +
      icon(tracked ? 'check' : 'plus') + '<span>' + (tracked ? action + 'ing' : action) + '</span></button>';
  }

  function ventureRow(venture, lead = "") {
    return '<article class="venture-row"><div><h4>' + escape(venture.name) + '</h4>' +
      '<p>' + escape([lead, venture.relationship].filter(Boolean).join(' · ')) + '</p>' +
      '<dl><div><dt>Operations</dt><dd>' + escape(venture.operations || "Unverified") + '</dd></div>' +
      '<div><dt>Funding</dt><dd>' + escape(venture.funding || "Unverified") + '</dd></div></dl>' +
      '<div class="venture-sources"><a class="source-link" href="' + escape(venture.roleSource) +
      '" target="_blank" rel="noopener noreferrer">Role source</a>' +
      (venture.evidenceSource ? '<a class="source-link" href="' + escape(venture.evidenceSource) +
        '" target="_blank" rel="noopener noreferrer">Activity and funding source</a>' : '') +
      '</div></div>' + trackButton(venture) + '</article>';
  }

  function currentVentureRow(venture, inCollection = false) {
    const shortlisted = inCollection && preferences.decisions[venture.id] === "shortlist";
    const followed = preferences.tracked[venture.id] === "track";
    return '<article class="venture-row current-venture-row"><div><div class="current-venture-title">' +
      '<button class="text-button project-link" type="button" data-open="' + escape(venture.id) + '">' +
      escape(venture.name) + '</button>' + (inCollection ? '' : trackButton(venture)) +
      '</div><p>' + escape(venture.focus) + '</p>' +
      (inCollection && venture.lead ? '<p>' + escape(venture.lead) + '</p>' : '') +
      '</div>' + (inCollection ? '<div class="saved-venture-actions">' + (followed ? trackButton(venture) : '') +
        (shortlisted ? shortlistButton(venture) : '') + '</div>' : '') + '</article>';
  }

  function personRow(person, showProjects = true) {
    const projectLinks = showProjects ? person.projects.map(id => currentVentureRow(currentVentures.get(id))).join("") : "";
    const profiles = person.profiles;
    const quickLinks = [["linkedin", "LinkedIn"], ["github", "GitHub"]].flatMap(([service, label]) =>
      profiles[service] ? ['<a class="person-social" href="' + escape(profiles[service]) +
        '" target="_blank" rel="noopener noreferrer" title="' + label + '" aria-label="' +
        escape(person.name + ' on ' + label) + '">' + icon(service) + '</a>'] : []
    ).join("");
    const profileLinks = [["Founder profile source", person.source], ...profiles.more].filter(([, url]) => url).map(([label, url]) =>
      '<a class="source-link" href="' + escape(url) + '" target="_blank" rel="noopener noreferrer">' +
      escape(label) + ' ' + icon('arrow-up-right') + '</a>'
    ).join("");
    const initials = person.name.split(/\s+/).map(name => name[0]).slice(0, 2).join("");
    return '<article class="person-row"><div class="person-identity">' +
      (showProjects ? '<span class="person-initials" aria-hidden="true">' + escape(initials) + '</span>' : '') +
      '<div><h3>' + escape(person.name) + '</h3><p class="person-role">' +
      escape(person.role) + '</p><p class="person-experience">' + escape(person.contribution) + '</p>' +
      (quickLinks ? '<div class="person-socials">' + quickLinks + '</div>' : '') + '</div></div>' +
      (showProjects ? '<div class="person-projects"><p class="person-projects-label">Current venture</p>' +
        projectLinks + '</div>' : '') +
      trackButton(person) + '<section class="person-ventures"><h4>Other ventures</h4>' +
      (person.ventures.length
        ? person.ventures.map(venture => ventureRow(venture)).join("")
        : '<p>No other ventures verified.</p>') +
      '</section>' + githubEvidence(person) + (recommendations[person.id]
      ? recommendationPanel(person.id, person.contribution, '<div class="person-profile-links">' + profileLinks + '</div>')
      : profileLinks ? '<div class="recommendation-panel person-profile-links">' + profileLinks + '</div>' : '') + '</article>';
  }

  function dateLabel(value) {
    if (!value) return "";
    return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(value + "T12:00:00Z"));
  }

  function evidenceText(signal) {
    if (signal.evidence) return signal.evidence;
    const source = sourceById.get(signal.source_id);
    if (source.backend || !signal.focus) return "";
    return source.publisher + " lists the team's work on " + signal.focus.charAt(0).toLowerCase() + signal.focus.slice(1) + ".";
  }

  function sourceLink(source) {
    let href;
    try {
      href = new URL(source.url);
      if (!["https:", "http:"].includes(href.protocol)) return "";
    } catch { return ""; }
    return '<a class="source-link" href="' + escape(href.href) +
      '" target="_blank" rel="noopener noreferrer">' + escape(rosterLabels[source.id] || source.publisher) + ' ' + icon('arrow-up-right') + '</a>';
  }

  function bestSignal(team) {
    return [...team.signals].sort((a, b) =>
      Number(Boolean(b.evidence)) - Number(Boolean(a.evidence)) ||
      (sourceById.get(b.source_id).event_on || "").localeCompare(sourceById.get(a.source_id).event_on || "")
    )[0];
  }

  function makeActions(team) {
    return '<div class="deck-actions decision-actions">' + ['pass', 'watch', 'shortlist'].map(action => {
      const active = preferences.decisions[team.id] === action;
      return '<button type="button" class="decision-button ' + action + (active ? ' is-active' : '') + '" data-decision="' + action +
        '" data-team="' + escape(team.id) + '" aria-pressed="' + active + '">' + icon(actionIcons[action]) + '<span>' + actionLabels[action] + '</span></button>';
    }).join("") + '</div>';
  }

  function emptyState(title, body, action) {
    return '<div class="empty-state"><h3>' + escape(title) + '</h3><p>' + escape(body) + '</p>' + (action || '') + '</div>';
  }

  const resetFiltersButton = '<button type="button" class="text-button" data-clear-filters>Clear filters</button>';
  function undoButton() {
    return '<button type="button" class="undo-button" data-undo' + (availableUndoIndex() >= 0 ? '' : ' disabled') + '>' + icon('undo') + ' Undo</button>';
  }

  function drawDeck(all) {
    const decisions = preferences.decisions;
    const remaining = all.filter(item => !decisions[item.id]);
    const item = remaining.find(entry => entry.id === preferredDeckId) || remaining[0];
    currentDeckId = item?.id || null;
    const header = '<div class="deck-meta"><span role="status">Projects · ' +
      (all.length - remaining.length) + ' / ' + all.length + ' reviewed</span>' + undoButton() + '</div>';
    if (!item) {
      deckArea.innerHTML = header + (all.length
        ? emptyState("Review complete", "Every project in this queue has a decision.", '<button type="button" class="outline" data-page="collection">Open Saved</button>')
        : emptyState("No matching projects", "Change your search or clear a filter to try again.", resetFiltersButton));
      return;
    }
    const next = remaining.find(entry => entry !== item);
    const nextTone = next && (sectorClasses[next.sector] || "digital");
    const content = leadCard(item) + makeActions(item);
    deckArea.innerHTML = header + '<div class="deck-stack' + (next ? ' has-next next-' + nextTone : '') + '"><div class="deck-card" data-card="' +
      escape(item.id) + '">' + content + '</div></div>' +
      '<p class="deck-help">← Pass · ↑ Watch · → Shortlist</p>';
  }

  function layoutVentureColumns(grid) {
    const style = getComputedStyle(grid);
    const columns = Number(style.getPropertyValue("--result-columns"));
    const rowSize = parseFloat(style.gridAutoRows);
    const gap = Math.round(parseFloat(style.columnGap) / rowSize);
    const cards = [...grid.children];
    const heights = cards.map(card => Math.max(1, Math.ceil(card.offsetHeight / rowSize)));
    const nextRows = Array(columns).fill(1);
    cards.forEach((card, index) => {
      const column = index % columns;
      card.style.gridColumn = String(column + 1);
      card.style.gridRow = nextRows[column] + " / span " + heights[index];
      nextRows[column] += heights[index] + gap;
    });
  }

  const ventureColumnsObserver = new ResizeObserver(entries => {
    const grid = entries[0]?.target.closest(".venture-stack");
    if (grid?.isConnected) layoutVentureColumns(grid);
  });

  function observeVentureColumns() {
    ventureColumnsObserver.disconnect();
    const grid = document.querySelector("section:not([hidden]) .results:not([hidden]) > .lead-card")?.parentElement;
    if (!grid) return;
    grid.classList.add("venture-stack");
    layoutVentureColumns(grid);
    for (const card of grid.children) ventureColumnsObserver.observe(card);
  }

  function drawCollection() {
    const trackedPeople = people.filter(person => preferences.tracked[person.id] === "track");
    const savedVentures = new Map();
    const watchedTeams = teams.filter(team => preferences.decisions[team.id] === "watch");
    for (const team of teams) {
      if (preferences.decisions[team.id] === "shortlist") savedVentures.set(team.id, currentVentures.get(team.id) || team);
    }
    for (const venture of ventures) {
      if (preferences.tracked[venture.id] === "track") savedVentures.set(venture.id, venture);
    }
    const counts = { tracked: trackedPeople.length, ventures: savedVentures.size, watch: watchedTeams.length };
    const labels = { tracked: "Tracked founders", ventures: "Saved ventures", watch: "Watching" };
    const tabs = '<div class="switch collection-tabs" aria-label="Saved views">' + Object.keys(labels).map(action =>
      '<button type="button" data-collection="' + action + '" aria-pressed="' + (collectionTab === action) + '">' +
      labels[action] + ' <span>' + counts[action] + '</span></button>'
    ).join("") + '</div>';
    const cards = collectionTab === 'tracked' ? trackedPeople.map(person => personRow(person)) :
      collectionTab === 'ventures' ? [...savedVentures.values()].map(venture => teamById.has(venture.id)
        ? currentVentureRow(venture, true) : ventureRow(venture, venture.lead)) : watchedTeams.map(leadCard);
    const hints = {
      watch: ['Your watchlist is empty', 'Choose Watch on a lead you want to revisit.'],
      tracked: ['No tracked founders', 'Track a founder in Explore to save their profile and ventures.'],
      ventures: ['No saved ventures', 'Follow or shortlist a venture to keep it here.']
    };
    byId("collectionArea").innerHTML = tabs + (cards.length
      ? '<div class="' + (['tracked', 'ventures'].includes(collectionTab) ? 'people-list' : 'results') + '">' + cards.join("") + '</div>'
      : emptyState(...hints[collectionTab], '<button type="button" class="outline" data-page="explore">Explore</button>'));
    byId("savedCount").textContent = String(counts.tracked + new Set([...savedVentures.keys(), ...watchedTeams.map(team => team.id)]).size);
    observeVentureColumns();
  }

  function matchReason(team) {
    const modelReason = sort.value === 'signal' && ranking?.key === JSON.stringify([query.value, sector.value, "all"])
      ? ranking.reasons[team.id] : null;
    if (modelReason) return modelReason;
    const record = index.find(entry => entry.team === team);
    const terms = queryTerms();
    const matched = terms.length && terms.every(term => [record.names, record.topics, record.body].some(field => field.includes(term)));
    return [(matched ? 'Matches ' + terms.join(' ') : team.focus), signalCount(team)].filter(Boolean).join(' · ');
  }

  function backendBrief(team, memo) {
    const drivers = (memo?.score_drivers || team.drivers).filter(driver => typeof driver === "string");
    const urls = [...new Set([...(memo?.evidence_urls || []), ...team.evidence, team.website].filter(Boolean))];
    const whyNow = team.whyNow && (memo?.investment_thesis?.why_now || team.whyNow);
    const list = drivers.length ? '<ul>' + drivers.map(driver => '<li>' + escape(driver) + '</li>').join("") + '</ul>' : '';
    const links = urls.map(url => {
      try { return sourceLink({ url, publisher: new URL(url).hostname }); }
      catch { return ""; }
    }).join(" ");
    if (!whyNow && !list && !links) return "";
    return '<details class="recommendation-panel"><summary><span>Why take a closer look?</span>' +
      '<span class="recommendation-toggle" aria-hidden="true"></span></summary>' +
      '<div class="recommendation-content">' + (whyNow ? '<p>' + escape(whyNow) + '</p>' : '') +
      list + (links ? '<div class="recommendation-evidence">' + links + '</div>' : '') + '</div></details>';
  }

  function bulletList(items) {
    const values = (Array.isArray(items) ? items : []).filter(value => typeof value === "string" && value.trim());
    return values.length ? '<ul>' + values.map(value => '<li>' + escape(value) + '</li>').join("") + '</ul>' : '';
  }

  function modelMemo(memo) {
    const brief = memo.ml_memo;
    if (!brief || typeof brief !== "object") return "";
    const signals = (Array.isArray(brief.top_signals) ? brief.top_signals : [])
      .filter(item => item && typeof item.signal === "string" && item.signal.trim()).map(item => {
        let link = "";
        try { link = item.source_url ? " " + sourceLink({ url: item.source_url, publisher: new URL(item.source_url).hostname }) : ""; }
        catch { link = ""; }
        return '<li>' + escape(item.signal) + link + '</li>';
      });
    const part = (title, value) => typeof value === "string" && value.trim()
      ? '<div><strong>' + title + '</strong><p>' + escape(value) + '</p></div>' : '';
    return '<div class="model-memo"><strong>Analyst brief</strong>' +
      (typeof brief.one_liner === "string" ? '<p>' + escape(brief.one_liner) + '</p>' : '') +
      (signals.length ? '<div><strong>Strongest signals</strong><ul>' + signals.join("") + '</ul></div>' : '') +
      part("Team", brief.team) + part("Why it is early", brief.why_early) +
      (brief.devils_advocate_risks?.length ? '<div><strong>Risks and unknowns</strong>' + bulletList(brief.devils_advocate_risks) + '</div>' : '') +
      part("Next step", brief.next_step) +
      '<p class="model-memo-note">' + (String(brief.memo_source || "").startsWith("openai")
        ? 'Written by a language model from the sourced facts above. The model score is a rank against a reference cohort, not a probability.'
        : 'Template summary of the sourced facts above.') + '</p></div>';
  }

  function memoSection(memo) {
    const founders = (memo.team_dossier?.founders || []).map(person => {
      const details = [person.role, ...(person.universities || [])].filter(Boolean).join(" · ");
      return '<li><strong>' + escape(person.full_name) + '</strong> ' + escape(details) +
        (person.linkedin_url ? ' ' + sourceLink({ publisher: "LinkedIn", url: person.linkedin_url }) : '') +
        bulletList(person.prior_companies) + '</li>';
    });
    const scores = [
      ["Overall sourcing score", memo.overall_score],
      ["Rules-based score", memo.deterministic_score],
      ["Model rank (0–100)", memo.ml_score],
      ["Technology score", memo.technology_moat?.moat_score],
      ["Commercial score", memo.commercial_traction?.commercial_score]
    ].filter(([, value]) => Number.isFinite(value));
    const facts = [
      ["Founded", memo.founding_year], ["Legal name", memo.legal_name], ["Register number", memo.commercial_traction?.register_number],
      ["Commercial signals", memo.commercial_traction?.status], ["IP status", memo.technology_moat?.ip_status]
    ].filter(([, value]) => value);
    const stats = [...facts, ...scores].map(([key, value]) => '<div><dt>' + escape(key) + '</dt><dd>' + escape(value) + '</dd></div>');
    return '<details class="recommendation-panel"><summary>Investment memo <span class="recommendation-toggle" aria-hidden="true"></span></summary>' +
      '<div class="recommendation-content">' +
      (memo.executive_summary ? '<p>' + escape(memo.executive_summary) + '</p>' : '') +
      (memo.investment_thesis?.why_now ? '<div><strong>Investment thesis</strong><p>' + escape(memo.investment_thesis.why_now) + '</p></div>' : '') +
      modelMemo(memo) +
      (stats.length ? '<dl class="sourcing-stats">' + stats.join("") + '</dl>' : '') +
      (founders.length ? '<div><strong>Founding team</strong><ul>' + founders.join("") + '</ul></div>' : '') +
      (memo.devils_advocate_risks?.length ? '<div><strong>Risks to verify</strong>' + bulletList(memo.devils_advocate_risks) + '</div>' : '') +
      (memo.lp_synergy_analysis?.length ? '<div><strong>Potential investor synergies</strong>' +
        bulletList(memo.lp_synergy_analysis.map(match => [match.lp_name, match.reason].filter(Boolean).join(" · "))) + '</div>' : '') +
      '</div></details>';
  }

  function githubEvidence(person) {
    if (!person.profiles.github || !connected) return "";
    const username = new URL(person.profiles.github).pathname.split("/").filter(Boolean)[0];
    return '<div class="github-inspection"><button type="button" class="text-button" data-github-check="' +
      escape(username) + '">Check GitHub activity</button><div role="status"></div></div>';
  }

  async function inspectGithub(button) {
    const output = button.nextElementSibling;
    button.disabled = true;
    output.textContent = "Checking GitHub…";
    try {
      const profile = await window.FOUNDERHUNT_API.github(button.dataset.githubCheck);
      output.innerHTML = '<p>' + escape(profile.public_repos ?? 0) + ' public repos · ' +
        escape(profile.total_stars ?? 0) + ' stars · ' + escape(profile.total_forks ?? 0) + ' forks</p>' +
        bulletList(profile.top_languages) +
        (profile.top_repos || []).slice(0, 3).map(repo => sourceLink({ publisher: repo.name, url: repo.url })).join(" ");
    } catch (error) {
      output.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  async function refreshDashboard() {
    const status = byId("pipelineStatus");
    status.textContent = "Loading backend metrics…";
    const [dashboard, health] = await Promise.allSettled([
      window.FOUNDERHUNT_API.dashboard(), window.FOUNDERHUNT_API.health()
    ]);
    const data = dashboard.value;
    const audit = data?.rejected_audit || [];
    const metrics = [
      ["Ranked ventures", data?.ranked_leads?.length],
      ["Rejected ventures", data ? audit.length : undefined],
      ["Ingested entities", health.value?.database?.total_entities],
      ["Recorded signals", health.value?.database?.total_signals],
      ["API status", health.value?.status],
      ["Enrichment spend", health.value?.cost_and_cache ? "$" +
        Number(health.value.cost_and_cache.cumulative_spend_usd).toFixed(2) : undefined]
    ];
    const entries = metrics.filter(([, value]) => value != null).map(([label, value]) =>
      '<div><dt>' + escape(label) + '</dt><dd>' + escape(value) + '</dd></div>');
    const rows = audit.map(entity => '<li><strong>' + escape(entity.canonical_name) + '</strong>' +
      bulletList(entity.kill_reasons) + '</li>').join("");
    byId("pipelineResults").innerHTML = '<dl class="sourcing-stats">' + entries.join("") + '</dl>' +
      (data ? '<details><summary>Screening funnel</summary><dl class="sourcing-stats">' +
        Object.entries(data.funnel || {}).map(([key, value]) =>
          '<div><dt>' + escape(key.replaceAll("_", " ")) + '</dt><dd>' + escape(value) + '</dd></div>').join("") +
        '</dl></details><details><summary>Rejection audit (' + audit.length + ')</summary>' +
        (rows ? '<ul class="sourcing-audit">' + rows + '</ul>' : '<p>No rejected ventures in the feed.</p>') + '</details>' : '');
    status.textContent = dashboard.status === "fulfilled" && health.status === "fulfilled"
      ? "" : "Some backend metrics are unavailable.";
  }

  byId("operatorToken").addEventListener("input", event => window.FOUNDERHUNT_API.setOperatorToken(event.target.value));
  byId("loadWorkspace").addEventListener("click", async () => {
    const status = byId("workspaceStatus");
    if (!window.confirm("Replace the saves in this browser with the shared workspace saves?")) return;
    try {
      const saved = await window.FOUNDERHUNT_API.savedWorkspace();
      preferences = { ...saved, history: [] };
      writePreferences();
      render();
      status.textContent = "Shared saves loaded into this browser.";
    } catch (error) { status.textContent = error.message; }
  });
  byId("saveWorkspace").addEventListener("click", async () => {
    const status = byId("workspaceStatus");
    if (!window.confirm("Replace the shared workspace saves with the saves in this browser?")) return;
    try {
      await window.FOUNDERHUNT_API.saveWorkspace({ decisions: preferences.decisions, tracked: preferences.tracked });
      status.textContent = "Shared workspace updated.";
    } catch (error) { status.textContent = error.message; }
  });
  byId("refreshFeed").addEventListener("click", () => location.reload());
  byId("enrichForm").addEventListener("submit", async event => {
    event.preventDefault();
    const status = byId("enrichStatus");
    const output = byId("enrichResult");
    const submit = event.submitter;
    const target = byId("ventureTarget").value.trim();
    if (!target) return;
    if (!byId("operatorToken").value.trim()) {
      status.textContent = "Enter the operator token to run a live evaluation.";
      byId("operatorToken").focus();
      return;
    }
    submit.disabled = true;
    status.textContent = "Researching public sources…";
    output.replaceChildren();
    try {
      const result = await window.FOUNDERHUNT_API.enrich(target);
      status.textContent = result.gate_passed ? "Added to the venture feed." : "Screened out.";
      output.innerHTML = '<h4>' + escape(result.canonical_name) + '</h4>' +
        (result.inflection_why_now ? '<p>' + escape(result.inflection_why_now) + '</p>' : '') +
        bulletList(result.kill_reasons) + bulletList((result.founders_enriched || []).map(founder => founder.full_name)) +
        (result.gate_passed ? '<button type="button" class="outline" data-reload-venture="' +
          escape(result.entity_id) + '">Open added venture</button>' : '');
    } catch (error) {
      status.textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  async function dispatchVenture(button) {
    const status = button.nextElementSibling;
    if (!window.confirm("Hand this venture to the configured n8n workflow?")) return;
    button.disabled = true;
    status.textContent = "Preparing handoff…";
    try {
      const result = await window.FOUNDERHUNT_API.dispatch(button.dataset.dispatch);
      status.textContent = result.status === "simulated_dispatch" ? "Preview only. No webhook is configured." : "Sent to n8n.";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  function renderDetail(team) {
    const timeline = ventureTimeline(team);
    const projectPeople = people.filter(person => person.projects.includes(team.id));
    const peopleSection = projectPeople.length
      ? '<section class="detail-people"><h3 class="evidence-label">People</h3><div class="people-list">' +
        projectPeople.map(person => personRow(person, false)).join("") + '</div></section>' : '';
    byId("detailContent").innerHTML = '<div class="detail-intro">' + sectorTag(team) +
      '<h2 id="detailTitle" class="detail-title">' + escape(team.name) + '</h2>' +
      '<div class="detail-facts">' + (location(team) || '<span>Location unknown</span>') + '<span>' + (team.funding ? escape(team.funding) : 'Funding unknown') + '</span><span>' + (collectionLabels[preferences.decisions[team.id]] || 'Unreviewed') + '</span></div>' +
      '<p class="lead-summary">' + escape(team.summary) + '</p><p class="match-reason">' + escape(matchReason(team)) + '</p><p class="detail-discovery"><strong>' + (team.entityId ? 'Discovered' : 'Discovered (demo)') + '</strong> ' + discoveryDate(team) + '</p></div>' +
      timelineSection(timeline) + peopleSection +
      (team.entityId ? '<div data-backend-memo>' + backendBrief(team) + '</div><div class="workflow-handoff"><button type="button" class="outline" data-dispatch="' +
        escape(team.entityId) + '">Hand off to n8n</button><span role="status"></span></div>' : recommendationPanel(team.id, evidenceText(bestSignal(team)))) +
      '<div class="detail-actions">' + makeActions(team) + '</div>';
    dialog.setAttribute("aria-labelledby", "detailTitle");
    if (team.entityId) {
      const brief = dialog.querySelector("[data-backend-memo]");
      window.FOUNDERHUNT_API.memo(team.entityId).then(memo => {
        if (!brief.isConnected || !dialog.open) return;
        const details = brief.querySelector("details");
        const summary = details?.querySelector("summary");
        const summaryFocused = document.activeElement === summary;
        if (!summaryFocused && brief.contains(document.activeElement)) return;
        const wasOpen = details?.open;
        brief.innerHTML = backendBrief(team, memo) + memoSection(memo);
        if (wasOpen) {
          const replacement = brief.querySelector("details");
          if (replacement) replacement.open = true;
        }
        if (summaryFocused) brief.querySelector("summary")?.focus({ preventScroll: true });
      }).catch(() => {});
    }
  }

  function render() {
    const matches = rankedTeams();
    const showPeople = kind === "people";
    const showEvents = kind === "events";
    const items = showPeople ? matchingPeople() : matches;
    byId("exploreCategory").textContent = showEvents ? "events" : showPeople ? "founders" : "ventures";
    const noun = showPeople ? (items.length === 1 ? "founder" : "founders") : (items.length === 1 ? "venture" : "ventures");
    byId("resultsCount").hidden = showEvents;
    byId("resultsCount").textContent = items.length + " " + noun;
    byId("eventsView").hidden = !showEvents;
    const results = byId("results");
    results.hidden = showEvents;
    results.className = showPeople ? "people-list founder-directory" : "results";
    results.innerHTML = items.length
      ? items.map(item => showPeople ? personRow(item) : leadCard(item)).join("")
      : emptyState("No matching " + (showPeople ? "founders" : "ventures"), "Try another keyword or clear your filters.", resetFiltersButton);
    document.querySelectorAll('[data-kind]').forEach(button => button.setAttribute("aria-pressed", String(button.dataset.kind === kind)));
    document.body.dataset.kind = kind;
    sort.querySelectorAll("[data-venture-sort]").forEach(option => { option.hidden = page === "explore" && showPeople; });
    byId("reviewStart").hidden = showPeople || showEvents;
    drawDeck(matches);
    drawCollection();
  }

  const pages = ["explore", "review", "collection", "sourcing"];

  let backendEvents = [];
  function renderEvents(type) {
    const events = [...window.SCOUT_EVENTS, ...backendEvents].filter(event => type === "all" || event.type === type);
    byId("eventCount").textContent = events.length + (events.length === 1 ? " programme" : " programmes");
    document.querySelectorAll("[data-event-type]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.eventType === type));
    });
    byId("eventResults").innerHTML = events.map(event => {
      const timing = event.date && event.date < today ? event.pastTiming : event.timing;
      const date = event.date ? ' <time datetime="' + escape(event.date) + '">' + dateLabel(event.date) + '</time>' : '';
      const projects = event.projects.map(([id, reason]) =>
        '<li><button type="button" class="text-button project-link" data-open="' + escape(id) + '">' +
        escape(teamById.get(id).name) + '</button><p>' + escape(reason) + '</p></li>'
      ).join("");
      return '<article class="event-row"><div class="event-overview"><div class="event-timing"><span class="event-kind ' +
        escape(event.type) + '">' + (event.type === "lab" ? "Venture lab" : event.type === "event" ? "Sourcing event" : "Competition") + '</span><span>' +
        escape(timing) + date + '</span></div><h3>' + escape(event.name) + '</h3><p class="event-organiser">' +
        escape([event.organiser, event.location].filter(Boolean).join(" · ")) + '</p><p class="event-description">' + escape(event.description) +
        '</p>' + sourceLink({ publisher: "Programme website", url: event.url }) + '</div>' +
        '<div class="event-projects"><h4>' + escape(event.projectsLabel) + '</h4><ul>' + projects + '</ul>' +
        (event.projectsSource ? sourceLink({ publisher: "Venture evidence", url: event.projectsSource }) : '') +
        '</div></article>';
    }).join("");
  }

  function showPage(next) {
    if (!pages.includes(next)) return;
    page = next;
    if (page === "explore" && kind === "people" && sort.selectedOptions[0]?.hasAttribute("data-venture-sort")) sort.value = "signal";
    document.body.dataset.page = next;
    pages.forEach(name => { byId(name + "View").hidden = name !== page; });
    document.querySelectorAll("[data-page]").forEach(button => {
      if (button === document.body) return;
      const active = button.dataset.page === next;
      button.classList.toggle("is-active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    render();
    if (next === "sourcing") refreshDashboard();
    window.scrollTo(0, 0);
    publishRankingRequest();
  }

  function clearFilters() {
    query.value = "";
    sector.value = "all";
    sort.value = "signal";
    applyFilters();
    query.focus();
  }

  function openDetail(id, trigger) {
    const team = teamById.get(id);
    if (!team) return;
    detailId = id;
    detailTrigger = trigger;
    renderDetail(team);
    if (!dialog.open) dialog.showModal();
  }

  function focusControl(selector, id, fallback) {
    const candidates = document.querySelectorAll(selector);
    const replacement = [...candidates].find(node => node.offsetWidth && Object.values(node.dataset).includes(id));
    (replacement || fallback)?.focus({ preventScroll: true });
  }

  function changeDecision(id, after, toggle = false, type = "team") {
    const isPerson = type === "person";
    const decisions = isPerson ? preferences.tracked : preferences.decisions;
    const item = (isPerson ? trackedById : teamById).get(id);
    if (!item || !(isPerson ? ["track"] : actions).includes(after)) return;
    const before = decisions[id] || null;
    if (toggle && before === after) after = null;
    if (before === after) return;
    if (after) decisions[id] = after;
    else delete decisions[id];
    preferences.history.push({ id, before, after, kind: type });
    if (preferences.history.length > 100) preferences.history.shift();
    preferredDeckId = null;
    const persistent = writePreferences();
    if (dialog.open && !isPerson) dialog.close();
    render();
    if (isPerson && dialog.open) {
      renderDetail(teamById.get(detailId));
      dialog.querySelector('[data-track="' + id + '"]')?.focus({ preventScroll: true });
    } else if (page === 'review') {
      deckArea.querySelector('.deck-card button, .undo-button:not(:disabled)')?.focus({ preventScroll: true });
    } else if (!detailId) {
      focusControl('[data-save],[data-track]', id, document.querySelector('[data-collection][aria-pressed="true"]'));
    }
    const label = ventureIds.has(id) ? (after ? "Following" : "Unfollowed") :
      isPerson ? (after ? "Tracking" : "Stopped tracking") : (collectionLabels[after] || "Decision cleared");
    showToast(label + ' · ' + item.name + (persistent ? '' : ' (this session only)'), true);
  }

  function undo() {
    const index = availableUndoIndex();
    if (index < 0) return;
    const [change] = preferences.history.splice(index, 1);
    const isPerson = change.kind === "person";
    const decisions = isPerson ? preferences.tracked : preferences.decisions;
    if (change.before) decisions[change.id] = change.before;
    else delete decisions[change.id];
    preferredDeckId = !isPerson && !change.before ? change.id : null;
    const persistent = writePreferences();
    render();
    if (page === "review") deckArea.querySelector('.deck-card button, .undo-button:not(:disabled)')?.focus({ preventScroll: true });
    const name = (isPerson ? trackedById : teamById).get(change.id).name;
    showToast('Decision undone · ' + name + (persistent ? '' : ' (this session only)'), availableUndoIndex() >= 0);
  }

  function applyFilters() {
    preferredDeckId = null;
    render();
    publishRankingRequest();
  }

  function publishRankingRequest() {
    if (page !== "review" && (page !== "explore" || kind !== "projects")) return;
    const criteria = { query: query.value, sector: sector.value, roster: "all" };
    window.dispatchEvent(new CustomEvent("firstsignal:rank", { detail: criteria }));
  }

  window.addEventListener("firstsignal:ranking", event => {
    const { query: search, sector: category, roster: cohort, ids, reasons = {} } = event.detail || {};
    if (search !== query.value || category !== sector.value || cohort !== "all" || !Array.isArray(ids)) return;
    const unique = [...new Set(ids)].filter(id => teamById.has(id));
    ranking = {
      key: JSON.stringify([search, category, cohort]),
      order: new Map(unique.map((id, position) => [id, position])),
      reasons: Object.fromEntries(Object.entries(reasons || {}).filter(([id, reason]) => teamById.has(id) && typeof reason === "string"))
    };
    render();
  });

  query.addEventListener("input", applyFilters);
  byId("searchForm").addEventListener("submit", event => { event.preventDefault(); applyFilters(); });
  [sector, sort].forEach(control => control.addEventListener("change", applyFilters));
  byId("reviewStart").addEventListener("click", () => showPage("review"));
  byId("closeDialog").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener("close", () => {
    if (detailTrigger?.isConnected) detailTrigger.focus({ preventScroll: true });
    else focusControl('[data-open]', detailId, page === "review"
      ? deckArea.querySelector('.deck-card button, .undo-button:not(:disabled)')
      : document.querySelector('.main-nav [aria-current]'));
    detailId = null;
    detailTrigger = null;
  });

  document.addEventListener("click", event => {
    const element = event.target.closest("button,a");
    if (!element) return;
    const { page: targetPage, collection, kind: nextKind, eventType, open, save, decision, team, track, githubCheck, dispatch, reloadVenture } = element.dataset;
    if (targetPage) { event.preventDefault(); showPage(targetPage); return; }
    if (collection) {
      collectionTab = collection;
      drawCollection();
      document.querySelector('[data-collection][aria-pressed="true"]').focus();
      return;
    }
    if (eventType) { renderEvents(eventType); return; }
    if (nextKind) {
      kind = nextKind;
      if (kind === "people" && sort.selectedOptions[0]?.hasAttribute("data-venture-sort")) sort.value = "signal";
      applyFilters();
      return;
    }
    if (reloadVenture) {
      try { sessionStorage.setItem("founderhunt-open-venture", "api:" + reloadVenture); } catch {}
      location.reload();
      return;
    }
    if (githubCheck) { inspectGithub(element); return; }
    if (dispatch) { dispatchVenture(element); return; }
    if (track) { changeDecision(track, "track", true, "person"); return; }
    if (open) { openDetail(open, element); return; }
    if (save) { changeDecision(save, "shortlist", true); return; }
    if (decision) { changeDecision(team, decision, dialog.open); return; }
    if (element.hasAttribute("data-undo")) { undo(); return; }
    if (element.hasAttribute("data-clear-filters")) clearFilters();
  });

  document.addEventListener("keydown", event => {
    if (event.ctrlKey || event.metaKey || event.altKey || dialog.open ||
      event.target.matches('input, textarea, select, [contenteditable="true"]')) return;
    if (event.key === '/' && ((page === 'explore' && kind !== 'events') || page === 'review')) {
      event.preventDefault();
      query.focus();
      return;
    }
    if (page !== "review" || !currentDeckId) return;
    const keys = { ArrowLeft: "pass", ArrowUp: "watch", ArrowRight: "shortlist" };
    if (!keys[event.key] || event.repeat) return;
    event.preventDefault();
    changeDecision(currentDeckId, keys[event.key]);
  });

  let swipeStart = null;
  deckArea.addEventListener("touchstart", event => {
    const card = event.target.closest("[data-card]");
    if (event.touches.length !== 1 || !card || event.target.closest("button,a,summary")) { swipeStart = null; return; }
    swipeStart = { id: card.dataset.card, x: event.touches[0].clientX, y: event.touches[0].clientY };
  }, { passive: true });
  deckArea.addEventListener("touchcancel", () => { swipeStart = null; });
  deckArea.addEventListener("touchend", event => {
    const start = swipeStart;
    swipeStart = null;
    if (!start || start.id !== currentDeckId || !event.changedTouches.length) return;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - start.x;
    const dy = touch.clientY - start.y;
    if (Math.abs(dx) < 70 || Math.abs(dx) <= Math.abs(dy) * 1.6) return;
    changeDecision(start.id, dx > 0 ? "shortlist" : "pass");
  }, { passive: true });

  window.addEventListener("storage", event => {
    if (event.key !== storageKey && event.key !== null) return;
    preferences = loadPreferences();
    render();
    if (detailId && dialog.open) {
      renderDetail(teamById.get(detailId));
      byId("closeDialog").focus();
    }
  });
  const preview = byId("heroFounder");
  preview.innerHTML = people.map(person => {
    const venture = teamById.get(person.projects[0]);
    return '<div class="preview-slide" role="group" aria-roledescription="slide" aria-hidden="true">' +
      '<p class="preview-label">Founder profile</p><h2>' + escape(person.name) + '</h2>' +
      '<p class="preview-role">' + escape(person.role) + '</p>' +
      '<p class="preview-experience">' + escape(person.contribution) + '</p>' +
      '<div class="preview-venture"><span>Venture</span><strong>' + escape(venture.name) +
      '</strong><p>' + escape(venture.focus) + '</p></div></div>';
  }).join("");
  const previewPosition = byId("previewPosition");
  let previewIndex = 0;
  let slideAnimations = [];
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

  function changePreview(step) {
    const slides = preview.children;
    if (!slides.length) return;
    slideAnimations.forEach(animation => animation.cancel());
    slideAnimations = [];
    const outgoing = slides[previewIndex];
    outgoing.setAttribute("aria-hidden", "true");
    previewIndex = (previewIndex + step + slides.length) % slides.length;
    const incoming = slides[previewIndex];
    const position = (previewIndex + 1) + " of " + slides.length;
    incoming.setAttribute("aria-label", position);
    incoming.setAttribute("aria-hidden", "false");
    previewPosition.textContent = position;
    previewPosition.parentElement.style.setProperty("--preview-progress", ((previewIndex + 1) / slides.length * 100) + "%");

    if (!step || slides.length < 2 || reducedMotion.matches || !incoming.animate) return;
    const direction = Math.sign(step);
    const options = { duration: 480, easing: "cubic-bezier(.22, 1, .36, 1)" };
    slideAnimations = [
      outgoing.animate([
        { visibility: "visible", translate: "0 0", opacity: 1 },
        { visibility: "visible", translate: (-direction * 78) + "% 0", opacity: 0, scale: .97 }
      ], options),
      incoming.animate([
        { translate: (direction * 88) + "% 0", opacity: .25, scale: .98 },
        { translate: "0 0", opacity: 1, scale: 1 }
      ], options),
      incoming.querySelector(".preview-venture").animate([
        { translate: (direction * 22) + "px 0", opacity: .25 },
        { translate: "0 0", opacity: 1 }
      ], { ...options, delay: 55, fill: "backwards" })
    ];
  }

  byId("previewPrevious").addEventListener("click", () => changePreview(-1));
  byId("previewNext").addEventListener("click", () => changePreview(1));
  preview.closest(".founder-preview").addEventListener("keydown", event => {
    if (event.altKey || event.ctrlKey || event.metaKey || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    changePreview(event.key === "ArrowRight" ? 1 : -1);
  });

  let previewTouch = null;
  preview.addEventListener("pointerdown", event => {
    if (event.pointerType !== "touch" || !event.isPrimary) return;
    previewTouch = { id: event.pointerId, x: event.clientX, y: event.clientY };
    preview.setPointerCapture(event.pointerId);
  });
  preview.addEventListener("pointercancel", () => { previewTouch = null; });
  preview.addEventListener("pointerup", event => {
    if (previewTouch?.id !== event.pointerId) return;
    const dx = event.clientX - previewTouch.x;
    const dy = event.clientY - previewTouch.y;
    previewTouch = null;
    if (Math.abs(dx) > 32 && Math.abs(dx) > Math.abs(dy) * 1.4) changePreview(dx < 0 ? 1 : -1);
  });

  changePreview(0);
  renderEvents("all");
  if (connected) {
    window.FOUNDERHUNT_API.events().then(({ events }) => {
      backendEvents = events.map(event => ({
        ...event, type: "event", date: event.date.slice(0, 10),
        timing: "Event date", pastTiming: "Past event",
        organiser: "Luma", description: "Event listing captured by the sourcing pipeline.",
        projectsLabel: "Linked organisers",
        projects: event.projects.map(([id, reason]) => ["api:" + id, reason]).filter(([id]) => teamById.has(id))
      })).filter(event => event.projects.length);
      renderEvents(document.querySelector('[data-event-type][aria-pressed="true"]').dataset.eventType);
    }).catch(() => {});
  }
  showPage("explore");
  try {
    const newVenture = sessionStorage.getItem("founderhunt-open-venture");
    sessionStorage.removeItem("founderhunt-open-venture");
    if (newVenture) openDetail(newVenture);
  } catch {}
})();
