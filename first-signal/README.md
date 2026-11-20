# FounderHunt

FounderHunt is a static browser app for the MAIN Hackathon HTGF challenge.

```sh
cd first-signal
python3 -m http.server 8000
```

Open http://localhost:8000. The app uses the bundled fonts and local data.

## Backend connection

From the repository root, install `backend/requirements.txt` with Python 3.11 or later, then run:

```sh
python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. FastAPI serves the frontend and API on the same origin. Its ranked feed comes from `data/wave2_frontend_feed.json`; accepted live evaluations are stored alongside it under `data/live_leads/`. An empty or unavailable feed leaves the bundled demo usable.

The **Sourcing** workspace shows feed health, screening and rejection metrics, live evaluation, and shared saves. It appears when the API responds. Set `FOUNDERHUNT_WRITE_TOKEN` on the server and enter the token in Sourcing to evaluate a venture, update shared saves, or hand a lead to a configured n8n workflow. Live evaluation also needs `OPENAI_API_KEY` and a positive `FOUNDERHUNT_ENRICH_BUDGET_USD`. Shared saves are an explicit workspace snapshot. Browser saves remain local until published from Sourcing.

### Deploy the connected app

Build and run the repository's Docker image on a Python-capable HTTPS host. Persist `/app/data` for feed updates, evaluation results, saves, and the budget ledger. Run a single API worker with the file-backed data and budget ledger.

```sh
docker build -t founderhunt .
docker run --rm --env-file .env -p 127.0.0.1:8000:8000 -v founderhunt-data:/app/data founderhunt
```

Use the existing Wave 2 pipeline to update batch-sourced ventures. Start with `python -m backend.run_wave2 --cache-only --max-budget-usd 0` for a run with paid enrichment disabled. The API reads the refreshed feed on its next request. Use **Refresh venture feed** to reload the browser list. GitHub checks use only linked handles and n8n handoffs run only after an explicit confirmation.

For a separately hosted frontend, set a `founderhunt-api` meta tag in `index.html` to the API's HTTPS origin and set `FOUNDERHUNT_ALLOWED_ORIGINS` to a JSON array containing the frontend origin.

## Frontend

- Explore opens on Ventures. Filter by sector, or search by person, project, venture, or technology.
- Edit `recommendations.js` to update prototype recommendations and diligence questions.
- Edit `people.js` to add source-backed profiles. Each profile links to project IDs from `roster.js`, which contains project details and public evidence.
- Tracked founders and saved ventures appear under Saved. Venture status is based on the linked public sources. Tracking and project decisions persist in browser storage using the existing `first-signal-review-v1` key.
- The project review deck supports Left to Pass, Right to Shortlist, and Up to Watch. Left and right swipes also work on touch screens.
- Best match uses local text matching and preserves backend ranking among equally relevant backend ventures. The existing `firstsignal:rank` and `firstsignal:ranking` events still work with `roster: "all"`.

## Ranking integration

`rankedTeams()` orders the Projects view and the review deck. The local baseline matches all meaningful query terms, prioritizing project and contributor names ahead of technology, sector, city, and evidence text.

An optional frontend adapter can listen for `firstsignal:rank` and dispatch `firstsignal:ranking` with the original criteria and a ranked result.

```js
window.addEventListener("firstsignal:rank", async ({ detail: criteria }) => {
  const { ids, reasons } = await fetchRanking(criteria);
  window.dispatchEvent(new CustomEvent("firstsignal:ranking", {
    detail: { ...criteria, ids, reasons }
  }));
});
```

Criteria are `{ query, sector, roster: "all" }`. Return an ordered array of project IDs from `roster.js` and optional evidence-based `reasons` keyed by project ID. Unknown and duplicate IDs are ignored. An empty array represents zero matches. On failure, leave the local baseline active by skipping the response event.
