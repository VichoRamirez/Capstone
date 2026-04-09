# SaaS ← codex_sebita Integration Summary

**Branch:** `saas-integration` (off `SaaS`)

## Backend changes

- New routing engine: `backend/models/routing/{heuristics,literature_heuristics,metaheuristics}.py`. Old `Heuristica.py / Metaheuristicas.py / HeuristicasLiteraturaBenchmark.py` deleted.
- New services: `road_routing.py` (OSRM), `fuel_price_service.py` (CLP/L API), `job_store.py` (async jobs), `traffic_factor_dataset_builder.py`.
- `optimizer_service.py` replaced wholesale with codex's OSRM-aware multi-day version.
- `cleaning_service.py` replaced with codex's stronger pipeline.
- New `db_persistence.py` bridges cleaned DataFrames → `VentaRepository` / `DetalleRepository`. Keeps DB writes out of `cleaning_service.py`.
- `schemas/optimization.py`: codex base + `user_id` field; `use_time_dependent_traffic` defaults to **False**.
- `api/router.py`: codex base merged with all SaaS endpoints. New endpoint set:
  - **codex:** `/health`, `/progress`, `/fuel/diesel-clp`, `/fuel/prices-clp`, `/validate-depot`, `/clean`, `/data/dashboard`, `/data/validate-addresses`, `/jobs/{id}/status`, `/jobs/{id}/result`, `/optimize`
  - **SaaS:** `/auth/{register,login,reset-password}`, `/catalog`, `/upload`, `/upload-detalle`, `/next-order-number`, `/simulation/order`
  - `/optimize` now accepts **optional** ventas/detalle — when omitted it falls back to `get_pending_orders_df(user_id)` from MySQL.
  - `/upload` and `/upload-detalle` run the codex cleaning pipeline and persist to MySQL via `db_persistence`.
  - Old `/optimize/status/{task_id}` and `/optimize/result/{task_id}` dropped (replaced by `/jobs/*`).

## Frontend changes

- `views/main_view.py` replaced with codex's 2800-line workspace UI. Patched:
  - `set_user(user_id, username)` method.
  - Username label in the header.
  - `user_id` injected into `OptimizerParams` payload at submit.
  - "Sync Ventas → DB" / "Sync Detalle → DB" buttons under the existing run row, wired to `UploadWorker` → `/upload`, `/upload-detalle`.
  - Dropped the "ventas/detalle file required" validation so optimization can fall back to DB.
- `workers/request_worker.py` replaced (async `/jobs/*` polling pattern).
- `workers/data_hub_worker.py` added.
- `workers/upload_worker.py` kept (powers DB sync).
- `frontend/app.py`: kept SaaS login stack, added codex's `qt_runtime.prepare_qt_platform_plugins()` call and the cleaner `shutdown()` pattern.
- `style.qss` and `widgets/components.py` taken from codex.

## Config / entry

- `app/main.py` and `app/qt_runtime.py` already matched codex.
- `config/settings.py`: kept SaaS DB block, added `API_HOST`, `API_PORT`, `OSRM_LOCAL_BASE_URL`, `OSRM_RESERVED_PORTS`.
- `.env.example`: appended OSRM vars.
- `requirements.txt`: merged — DB stack + Qt WebEngine + folium + python-multipart + osmnx + bcrypt.

## Smoke tests passed

- `backend.api.router` imports cleanly, 19 router endpoints registered.
- `backend.main:app` boots, 23 total routes (incl. `/docs` etc.).
- All new services + repositories import.
- All frontend workers, `login_view`, `main_view`, `frontend.app` import.

## Status

Nothing committed yet — changes are staged/unstaged on the `saas-integration` branch so they can be reviewed before commit.

## Things to verify before going live

1. Run the actual app end-to-end (login → upload → DB sync → optimize via DB fallback). Only import smoke tests were performed, not a runtime pass.
2. Confirm the `Venta` / `Detalle` ORM model `id_usuario` column actually accepts the writes — it is set post-upsert if the attribute exists.
3. The codex `main_view` references resources under `cache/`, `CLEAN_DATA/`, `RAW_DATA/`, `TEST_DATA/` at the project root — confirm those exist or the form-config persistence will silently no-op.
