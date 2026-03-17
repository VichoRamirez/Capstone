"""
Solucion final de orquestacion de heuristicas + metaheuristicas.

Requisitos cubiertos:
- Un solo archivo ejecutable.
- Crea y usa una base de datos SQLite en la misma carpeta del proyecto.
- Extrae de la BD: datos de instancia, configuraciones generales,
  configuraciones de heuristicas y metaheuristicas, y orden de prioridad.
- Ejecuta 6 heuristicas x 3 metaheuristicas = 18 combinaciones.
- Prioridad #1 fija: SOLOMON_I1_STYLE + TABU_SEARCH.
- Modo top_n:
  top_n=1 -> ejecuta solo la combinacion prioritaria #1.
  top_n=2 -> ejecuta #1 y #2.
  ...
  top_n=18 -> ejecuta todas.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from Heuristica import generate_toy_data
from HeuristicasLiteraturaBenchmark import (
    ProblemContext,
    heuristic_actual_textual,
    heuristic_alns_lite_rp,
    heuristic_cw_multistart_mejorada,
    heuristic_regret2_parallel,
    heuristic_solomon_i1_style,
    heuristic_sweep_gm74,
)
from Metaheuristicas import (
    EPS,
    GAConfig,
    LocalSearchConfig,
    PenaltyConfig,
    SAConfig,
    SolutionEvaluation,
    SolutionRoutes,
    TabuConfig,
    VRPTWData,
    clone_solution,
    genetic_algorithm_vrptw,
    simulated_annealing_vrptw,
    tabu_search_vrptw,
)


DEFAULT_DB_NAME = "solucion_heuristica.db"
INSTANCE_ID_DEFAULT = "default_150_15_42"

HEURISTICS = [
    "SOLOMON_I1_STYLE",
    "ACTUAL_TEXTUAL",
    "CW_MULTISTART_MEJORADA",
    "REGRET2_PARALLEL",
    "SWEEP_GM74",
    "ALNS_LITE_RP",
]

METAHEURISTICS = [
    "TABU_SEARCH",
    "GENETIC_ALGORITHM",
    "SIMULATED_ANNEALING",
]

# Orden de prioridad exigido: primera combinacion es Solomon + Tabu.
COMBINATION_PRIORITY = [
    ("SOLOMON_I1_STYLE", "TABU_SEARCH"),
    ("SOLOMON_I1_STYLE", "GENETIC_ALGORITHM"),
    ("SOLOMON_I1_STYLE", "SIMULATED_ANNEALING"),
    ("ACTUAL_TEXTUAL", "TABU_SEARCH"),
    ("ACTUAL_TEXTUAL", "GENETIC_ALGORITHM"),
    ("ACTUAL_TEXTUAL", "SIMULATED_ANNEALING"),
    ("CW_MULTISTART_MEJORADA", "TABU_SEARCH"),
    ("CW_MULTISTART_MEJORADA", "GENETIC_ALGORITHM"),
    ("CW_MULTISTART_MEJORADA", "SIMULATED_ANNEALING"),
    ("REGRET2_PARALLEL", "TABU_SEARCH"),
    ("REGRET2_PARALLEL", "GENETIC_ALGORITHM"),
    ("REGRET2_PARALLEL", "SIMULATED_ANNEALING"),
    ("SWEEP_GM74", "TABU_SEARCH"),
    ("SWEEP_GM74", "GENETIC_ALGORITHM"),
    ("SWEEP_GM74", "SIMULATED_ANNEALING"),
    ("ALNS_LITE_RP", "TABU_SEARCH"),
    ("ALNS_LITE_RP", "GENETIC_ALGORITHM"),
    ("ALNS_LITE_RP", "SIMULATED_ANNEALING"),
]


def _encode_tuple_key_dict(data: Dict[Tuple[int, int], float]) -> Dict[str, float]:
    return {f"{i},{j}": float(v) for (i, j), v in data.items()}


def _decode_tuple_key_dict(data: Dict[str, float]) -> Dict[Tuple[int, int], float]:
    out: Dict[Tuple[int, int], float] = {}
    for k, v in data.items():
        i_str, j_str = k.split(",")
        out[(int(i_str), int(j_str))] = float(v)
    return out


def _encode_int_key_dict(data: Dict[int, float]) -> Dict[str, float]:
    return {str(k): float(v) for k, v in data.items()}


def _decode_int_key_dict(data: Dict[str, float]) -> Dict[int, float]:
    return {int(k): float(v) for k, v in data.items()}


def _encode_coords(coords: Dict[int, Tuple[int, int]]) -> Dict[str, List[int]]:
    return {str(k): [int(v[0]), int(v[1])] for k, v in coords.items()}


def _decode_coords(coords: Dict[str, List[int]]) -> Dict[int, Tuple[int, int]]:
    return {int(k): (int(v[0]), int(v[1])) for k, v in coords.items()}


def _json_set(cur: sqlite3.Cursor, table: str, key_col: str, key: str, value_col: str, value_obj) -> None:
    cur.execute(
        f"""
        INSERT INTO {table} ({key_col}, {value_col})
        VALUES (?, ?)
        ON CONFLICT({key_col}) DO UPDATE SET {value_col}=excluded.{value_col}
        """,
        (key, json.dumps(value_obj)),
    )


def _json_get(cur: sqlite3.Cursor, table: str, key_col: str, key: str, value_col: str):
    row = cur.execute(
        f"SELECT {value_col} FROM {table} WHERE {key_col} = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def _generate_instance_payload(n_customers: int, n_trucks: int, seed: int, use_time_windows: bool) -> Dict:
    K, J, N, coords, p, v, T, P, V, c_fixed, g, o, d, t, max_route_time = generate_toy_data(
        n_customers=n_customers,
        n_trucks=n_trucks,
        seed=seed,
    )

    if use_time_windows:
        tw_open = {0: 0.0}
        tw_close = {0: float(max_route_time)}
        for j in J:
            earliest = t[0, j]
            latest = max(earliest, max_route_time - T[j] - t[j, 0])
            tw_open[j] = float(earliest)
            tw_close[j] = float(latest)
    else:
        tw_open = {0: 0.0}
        tw_close = {0: float(max_route_time)}
        for j in J:
            tw_open[j] = 0.0
            tw_close[j] = float(max_route_time)

    payload = {
        "K": [int(k) for k in K],
        "J": [int(j) for j in J],
        "N": [int(n) for n in N],
        "coords": _encode_coords(coords),
        "p": _encode_int_key_dict(p),
        "v": _encode_int_key_dict(v),
        "T": _encode_int_key_dict(T),
        "P": float(P),
        "V": float(V),
        "c_fixed": float(c_fixed),
        "g": float(g),
        "o": float(o),
        "d": _encode_tuple_key_dict(d),
        "t": _encode_tuple_key_dict(t),
        "max_route_time": float(max_route_time),
        "tw_open": _encode_int_key_dict(tw_open),
        "tw_close": _encode_int_key_dict(tw_close),
        "use_time_windows": bool(use_time_windows),
        "depot": 0,
    }
    return payload


def ensure_database(db_path: Path, force_reseed: bool = False) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as con:
        cur = con.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS general_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS heuristic_config (
                name TEXT PRIMARY KEY,
                config_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS metaheuristic_config (
                name TEXT PRIMARY KEY,
                config_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS combination_priority (
                rank INTEGER PRIMARY KEY,
                heuristic_name TEXT NOT NULL,
                meta_name TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS instance_store (
                instance_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        if force_reseed:
            cur.execute("DELETE FROM general_config")
            cur.execute("DELETE FROM heuristic_config")
            cur.execute("DELETE FROM metaheuristic_config")
            cur.execute("DELETE FROM combination_priority")
            cur.execute("DELETE FROM instance_store")

        # Configuracion general (defaults del proyecto).
        defaults_general = {
            "instance_id": INSTANCE_ID_DEFAULT,
            "n_customers": 150,
            "n_trucks": 15,
            "seed": 42,
            "use_time_windows": False,
            "top_n_default": 18,
            "penalty_config": {
                "cap_weight": 0.0,
                "cap_volume": 0.0,
                "time_window": 0.0,
                "route_duration": 0.0,
                "visit": 1_000_000.0,
                "fleet": 1_000_000.0,
                "unknown_node": 1_000_000.0,
            },
            "local_search_config": {
                "max_passes": 4,
                "max_neighbors_per_operator": 450,
                "first_improvement": False,
                "restart_from_first_operator_on_improve": True,
            },
        }
        for k, v in defaults_general.items():
            exists = cur.execute("SELECT 1 FROM general_config WHERE key = ?", (k,)).fetchone()
            if exists is None:
                _json_set(cur, "general_config", "key", k, "value", v)

        heur_cfg_defaults = {
            "ACTUAL_TEXTUAL": {"seed_offset": 0},
            "CW_MULTISTART_MEJORADA": {"starts": 16, "perturb_moves": 6},
            "SOLOMON_I1_STYLE": {},
            "REGRET2_PARALLEL": {},
            "SWEEP_GM74": {},
            "ALNS_LITE_RP": {"time_limit_sec": 35.0},
        }
        for name, cfg in heur_cfg_defaults.items():
            cur.execute(
                """
                INSERT INTO heuristic_config (name, config_json)
                VALUES (?, ?)
                ON CONFLICT(name) DO NOTHING
                """,
                (name, json.dumps(cfg)),
            )

        meta_cfg_defaults = {
            "GENETIC_ALGORITHM": {
                "population_size": 24,
                "generations": 40,
                "elite_size": 3,
                "crossover_rate": 0.9,
                "mutation_rate": 0.4,
                "mutation_strength_min": 1,
                "mutation_strength_max": 3,
                "tournament_size": 3,
                "local_search_probability": 0.35,
                "max_seconds": 18.0,
            },
            "SIMULATED_ANNEALING": {
                "initial_temp": 250.0,
                "cooling": 0.995,
                "final_temp": 0.5,
                "iters_per_temp": 70,
                "restart_stagnation": 350,
                "local_search_every": 180,
                "max_seconds": 12.0,
            },
            "TABU_SEARCH": {
                "iterations": 240,
                "neighborhood_size": 55,
                "tabu_tenure": 18,
                "diversification_gap": 45,
                "intensify_every": 35,
                "max_seconds": 12.0,
            },
        }
        for name, cfg in meta_cfg_defaults.items():
            cur.execute(
                """
                INSERT INTO metaheuristic_config (name, config_json)
                VALUES (?, ?)
                ON CONFLICT(name) DO NOTHING
                """,
                (name, json.dumps(cfg)),
            )

        exists_priority = cur.execute("SELECT COUNT(*) FROM combination_priority").fetchone()[0]
        if exists_priority == 0:
            for idx, (h_name, m_name) in enumerate(COMBINATION_PRIORITY, start=1):
                cur.execute(
                    """
                    INSERT INTO combination_priority (rank, heuristic_name, meta_name)
                    VALUES (?, ?, ?)
                    """,
                    (idx, h_name, m_name),
                )

        # Instancia base 150/15/42 persistida en BD.
        instance_id = _json_get(cur, "general_config", "key", "instance_id", "value")
        n_customers = int(_json_get(cur, "general_config", "key", "n_customers", "value"))
        n_trucks = int(_json_get(cur, "general_config", "key", "n_trucks", "value"))
        seed = int(_json_get(cur, "general_config", "key", "seed", "value"))
        use_tw = bool(_json_get(cur, "general_config", "key", "use_time_windows", "value"))

        row = cur.execute(
            "SELECT 1 FROM instance_store WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if row is None:
            payload = _generate_instance_payload(n_customers, n_trucks, seed, use_tw)
            cur.execute(
                """
                INSERT INTO instance_store (instance_id, payload_json)
                VALUES (?, ?)
                """,
                (instance_id, json.dumps(payload)),
            )

        con.commit()


def load_all_configuration(db_path: Path):
    with sqlite3.connect(str(db_path)) as con:
        cur = con.cursor()

        general_rows = cur.execute("SELECT key, value FROM general_config").fetchall()
        general_cfg = {k: json.loads(v) for k, v in general_rows}

        heur_rows = cur.execute("SELECT name, config_json FROM heuristic_config").fetchall()
        heur_cfg = {name: json.loads(cfg_json) for name, cfg_json in heur_rows}

        meta_rows = cur.execute("SELECT name, config_json FROM metaheuristic_config").fetchall()
        meta_cfg = {name: json.loads(cfg_json) for name, cfg_json in meta_rows}

        priority_rows = cur.execute(
            "SELECT rank, heuristic_name, meta_name FROM combination_priority ORDER BY rank ASC"
        ).fetchall()
        priorities = [(int(r), h, m) for r, h, m in priority_rows]

        instance_id = general_cfg["instance_id"]
        payload_row = cur.execute(
            "SELECT payload_json FROM instance_store WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if payload_row is None:
            raise RuntimeError(f"No existe payload para instance_id={instance_id} en la BD.")
        payload = json.loads(payload_row[0])

    return general_cfg, heur_cfg, meta_cfg, priorities, payload


def build_context_from_payload(payload: Dict) -> ProblemContext:
    data = VRPTWData(
        K=[int(x) for x in payload["K"]],
        J=[int(x) for x in payload["J"]],
        N=[int(x) for x in payload["N"]],
        p=_decode_int_key_dict(payload["p"]),
        v=_decode_int_key_dict(payload["v"]),
        T=_decode_int_key_dict(payload["T"]),
        P=float(payload["P"]),
        V=float(payload["V"]),
        c_fixed=float(payload["c_fixed"]),
        g=float(payload["g"]),
        o=float(payload["o"]),
        d=_decode_tuple_key_dict(payload["d"]),
        t=_decode_tuple_key_dict(payload["t"]),
        max_route_time=float(payload["max_route_time"]),
        tw_open=_decode_int_key_dict(payload["tw_open"]),
        tw_close=_decode_int_key_dict(payload["tw_close"]),
        use_time_windows=bool(payload.get("use_time_windows", False)),
        depot=int(payload.get("depot", 0)),
    )
    coords = _decode_coords(payload["coords"])
    return ProblemContext(data=data, coords=coords)


def run_heuristic(
    name: str,
    ctx: ProblemContext,
    cfg: Dict,
    base_seed: int,
) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    if name == "ACTUAL_TEXTUAL":
        return heuristic_actual_textual(ctx, seed=base_seed + int(cfg.get("seed_offset", 0)))
    if name == "CW_MULTISTART_MEJORADA":
        return heuristic_cw_multistart_mejorada(
            ctx,
            seed=base_seed,
            starts=int(cfg.get("starts", 16)),
            perturb_moves=int(cfg.get("perturb_moves", 6)),
        )
    if name == "SOLOMON_I1_STYLE":
        return heuristic_solomon_i1_style(ctx, seed=base_seed)
    if name == "REGRET2_PARALLEL":
        return heuristic_regret2_parallel(ctx, seed=base_seed)
    if name == "SWEEP_GM74":
        return heuristic_sweep_gm74(ctx, seed=base_seed)
    if name == "ALNS_LITE_RP":
        return heuristic_alns_lite_rp(
            ctx,
            seed=base_seed,
            time_limit_sec=float(cfg.get("time_limit_sec", 35.0)),
        )
    raise ValueError(f"Heuristica no soportada: {name}")


def run_metaheuristic(
    name: str,
    data: VRPTWData,
    initial_solution: Sequence[Sequence[int]],
    penalty_cfg: PenaltyConfig,
    ls_cfg: LocalSearchConfig,
    meta_cfg: Dict,
    seed: int,
) -> Tuple[SolutionRoutes, SolutionEvaluation]:
    initial_copy = clone_solution(initial_solution)
    if name == "GENETIC_ALGORITHM":
        cfg = GAConfig(**meta_cfg)
        return genetic_algorithm_vrptw(
            data,
            initial_solution=initial_copy,
            penalties=penalty_cfg,
            ga_config=cfg,
            ls_config=ls_cfg,
            seed=seed,
        )
    if name == "SIMULATED_ANNEALING":
        cfg = SAConfig(**meta_cfg)
        return simulated_annealing_vrptw(
            data,
            initial_solution=initial_copy,
            penalties=penalty_cfg,
            sa_config=cfg,
            ls_config=ls_cfg,
            seed=seed,
        )
    if name == "TABU_SEARCH":
        cfg = TabuConfig(**meta_cfg)
        return tabu_search_vrptw(
            data,
            initial_solution=initial_copy,
            penalties=penalty_cfg,
            tabu_config=cfg,
            ls_config=ls_cfg,
            seed=seed,
        )
    raise ValueError(f"Metaheuristica no soportada: {name}")


def _format_row(
    rank: int,
    heur_name: str,
    meta_name: str,
    ev: SolutionEvaluation,
    t_heur: float,
    t_meta: float,
    t_total: float,
) -> str:
    return (
        f"{rank:2d}. {heur_name:22s} + {meta_name:20s} | "
        f"obj={ev.cost_base:10.2f} | rutas={ev.route_count:3d} | factible={str(ev.feasible):5s} | "
        f"t_heur={t_heur:7.2f}s | t_meta={t_meta:7.2f}s | t_total={t_total:7.2f}s"
    )


def execute_combinations(
    ctx: ProblemContext,
    general_cfg: Dict,
    heur_cfg: Dict[str, Dict],
    meta_cfg: Dict[str, Dict],
    priorities: List[Tuple[int, str, str]],
    top_n: int,
) -> None:
    if top_n < 1:
        raise ValueError("top_n debe ser >= 1.")
    top_n = min(top_n, len(priorities))

    penalty_cfg = PenaltyConfig(**general_cfg["penalty_config"])
    ls_cfg = LocalSearchConfig(**general_cfg["local_search_config"])
    base_seed = int(general_cfg["seed"])

    print("\n=== EJECUCION DE COMBINACIONES HEURISTICA + METAHEURISTICA ===")
    print(
        f"Instancia: clientes={len(ctx.data.J)}, camiones={len(ctx.data.K)}, seed={base_seed}, "
        f"use_time_windows={ctx.data.use_time_windows}"
    )
    print(f"Ejecutando top_n={top_n} de {len(priorities)} combinaciones.")

    heuristic_cache: Dict[str, Tuple[SolutionRoutes, SolutionEvaluation, float]] = {}
    results: List[Tuple[int, str, str, SolutionEvaluation, float, float, float]] = []

    for rank, heur_name, meta_name in priorities[:top_n]:
        if heur_name not in heuristic_cache:
            t0 = time.time()
            sol_h, ev_h = run_heuristic(
                name=heur_name,
                ctx=ctx,
                cfg=heur_cfg.get(heur_name, {}),
                base_seed=base_seed + rank,
            )
            t_h = time.time() - t0
            heuristic_cache[heur_name] = (sol_h, ev_h, t_h)
        else:
            sol_h, ev_h, t_h = heuristic_cache[heur_name]

        t1 = time.time()
        sol_m, ev_m = run_metaheuristic(
            name=meta_name,
            data=ctx.data,
            initial_solution=sol_h,
            penalty_cfg=penalty_cfg,
            ls_cfg=ls_cfg,
            meta_cfg=meta_cfg.get(meta_name, {}),
            seed=base_seed + 1000 + rank,
        )
        t_m = time.time() - t1
        t_total = t_h + t_m

        results.append((rank, heur_name, meta_name, ev_m, t_h, t_m, t_total))
        print(_format_row(rank, heur_name, meta_name, ev_m, t_h, t_m, t_total))

    feasible = [r for r in results if r[3].feasible]
    if not feasible:
        print("\nNo hubo combinaciones factibles.")
        return

    best = min(feasible, key=lambda x: x[3].cost_base)
    print(
        "\nMejor combinacion ejecutada: "
        f"#{best[0]} {best[1]} + {best[2]} | obj={best[3].cost_base:.2f} | rutas={best[3].route_count}"
    )

    ranking = sorted(feasible, key=lambda x: x[3].cost_base)
    print("\n--- Ranking factible (por objetivo) ---")
    for idx, row in enumerate(ranking, start=1):
        rank, heur_name, meta_name, ev, t_h, t_m, t_total = row
        print(
            f"{idx:2d}) prioridad={rank:2d} | {heur_name:22s} + {meta_name:20s} "
            f"| obj={ev.cost_base:10.2f} | rutas={ev.route_count:3d} | t_total={t_total:7.2f}s"
        )


def list_priorities(priorities: List[Tuple[int, str, str]]) -> None:
    print("\n=== PRIORIDAD DE COMBINACIONES (18) ===")
    for rank, heur_name, meta_name in priorities:
        print(f"{rank:2d}. {heur_name} + {meta_name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Orquestador final: 6 heuristicas x 3 metaheuristicas (18 combinaciones)."
    )
    parser.add_argument("--db_path", type=str, default=str(Path(__file__).with_name(DEFAULT_DB_NAME)))
    parser.add_argument("--top_n", type=int, default=None)
    parser.add_argument("--list_only", action="store_true", help="Solo lista prioridades y termina.")
    parser.add_argument(
        "--reseed_db",
        action="store_true",
        help="Recrea seeds/configs/instancia por defecto en la BD.",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    ensure_database(db_path, force_reseed=args.reseed_db)
    general_cfg, heur_cfg, meta_cfg, priorities, payload = load_all_configuration(db_path)
    ctx = build_context_from_payload(payload)

    if args.list_only:
        list_priorities(priorities)
        return

    top_n = int(general_cfg["top_n_default"]) if args.top_n is None else int(args.top_n)
    execute_combinations(
        ctx=ctx,
        general_cfg=general_cfg,
        heur_cfg=heur_cfg,
        meta_cfg=meta_cfg,
        priorities=priorities,
        top_n=top_n,
    )


if __name__ == "__main__":
    main()

