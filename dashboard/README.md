# Factory dashboard (Grafana)

A local Grafana that shows, per product: how long the plan said it would take, how much time has been spent, what's left, the forecast, the biggest deviations, and whether the estimates are getting better.

It only **reads**. The factory works the same with or without it.

## Start

```bash
cd dashboard
docker compose up -d
```

Open http://localhost:3000. No login is needed to view; the admin login is `admin` / `admin` (Grafana asks you to change it). The first start downloads Grafana and its SQLite plugin, so it needs internet once.

Stop it with `docker compose down`. The dashboard's own settings survive in the `grafana-data` volume; the factory data lives outside it.

## Where the data comes from

1. `/factory:plan` puts estimates in each task issue: `**Estimate:** agent 2h · human 6h`.
2. `/factory:build` (and the developer and QA agents themselves) record when each stage starts and stops in the product repo's `.factory/timelog.jsonl`. QA also records its after-the-fact human-dev estimate. Your manual checks are timed with `/factory:qa manual`.
3. `gate time export` combines both into one SQLite file, `~/.factory/metrics/factory.db`. It runs automatically at the end of `/factory:status` and `/factory:build`. All your products share the file; pick one at the top of the dashboard.

If your metrics live elsewhere, set `FACTORY_METRICS_DIR` for both the factory (environment variable) and Grafana (copy `.env.example` to `.env`).

The dashboard only shows data after the first export. If a panel says "no data" or the datasource can't open the file, run `/factory:status` in the product repo once.

## What's on it

All figures are in hours. Agent hours and your hours are never added together.

| Panel | Shows |
| --- | --- |
| Tasks | items done out of the total |
| Agent hours | the factory's developers, QA and fix rounds: estimate, spent, remaining, forecast |
| Your hours (manual QA) | your manual checks: estimate, spent, remaining, forecast |
| Tasks table | every factory task and manual check: type, version, story, status, estimate, spent, remaining, forecast, deviation and re-estimates. Most urgent first (failed, blocked, in progress, ...) |
| Burn-down | per day: agent and your hours remaining (solid) and spent (dashed) |

**Filters** at the top: **Version** (each change request's version; `baseline` for the original cycle), **Status** and **Type** (Backend, Frontend, Bug, Manual QA). They apply to every panel. Each table column can also be filtered with the funnel on its header.

The forecast is the remaining work corrected with this product's history (how much items in the same category usually overrun).

## Changing the dashboard

The JSON in `dashboards/` is the source of truth, and edits in the Grafana UI are not saved. To change a panel: edit it in the UI, use *Export > Export as JSON*, and replace `dashboards/factory-time.json` with the result.
