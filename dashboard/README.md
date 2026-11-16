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
2. `/factory:build` (and the developer and QA agents themselves) record when each stage starts and stops in the product repo's `.factory/timelog.jsonl`. QA also records its after-the-fact human-dev estimate.
3. `gate time export` combines both into one SQLite file, `~/.factory/metrics/factory.db`. It runs automatically at the end of `/factory:status` and `/factory:build`. All your products share the file; pick one at the top of the dashboard.

If your metrics live elsewhere, set `FACTORY_METRICS_DIR` for both the factory (environment variable) and Grafana (copy `.env.example` to `.env`).

The dashboard only shows data after the first export. If a panel says "no data" or the datasource can't open the file, run `/factory:status` in the product repo once.

## Panels

| Section | Panel | Answers |
| --- | --- | --- |
| Where we are | Progress | Tasks done, estimate, spent, remaining by estimate, calibrated forecast, human-dev estimate |
| | Burn-down | Remaining estimate vs cumulative time spent, per day |
| | Estimate vs actual by milestone / story | Where the plan is holding or slipping |
| | Open tasks | What's left, with forecasts and re-estimates |
| Deviations | Done tasks | Estimate vs actual per task, worst first, split into dev / QA / fix rounds |
| | Where the agent time goes | Dev vs QA vs fix rounds |
| | Deviation by category | be/fe, feature/ops/sync/skeleton/bug, S/M |
| Learning | Estimation accuracy over time | Each finished task's actual/estimate and the rolling median; it should move towards 1.0 |
| | Calibration used by /factory:plan | The factors the planner applies to new estimates |
| | Human-dev: plan vs QA | The plan's human-dev estimate vs QA's estimate after seeing the PR |

The **Scope** picker filters to the baseline (`main`) or one change request.

## Changing the dashboard

The JSON in `dashboards/` is the source of truth, and edits in the Grafana UI are not saved. To change a panel: edit it in the UI, use *Export > Export as JSON*, and replace `dashboards/factory-time.json` with the result.
