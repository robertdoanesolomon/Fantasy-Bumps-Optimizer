"""Local web UI for Fantasy Bumps optimizer → http://127.0.0.1:5050

Run via ./run.sh (opens browser) or ./run_web.sh (see ./run_web.sh --help)."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request

from fantasy_bumps_logic import (
    crews_for_ui,
    df_to_json_rows,
    fetch_event,
    latest_year_for_series,
    run_optimization,
)

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/api/load")
def api_load():
    data = request.get_json(force=True, silent=True) or {}
    series = data.get("series") or "eights"
    use_latest = bool(data.get("use_latest"))
    try:
        year = int(data.get("year") or 2025)
    except (TypeError, ValueError):
        year = 2025
    if use_latest:
        y = latest_year_for_series(series)
        if y is None:
            return jsonify({"ok": False, "error": "Could not detect latest year."}), 400
        year = y
    try:
        men_df, mi = fetch_event(series, year, "men")
        women_df, wi = fetch_event(series, year, "women")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502

    men_ui = crews_for_ui(men_df, mi)
    women_ui = crews_for_ui(women_df, wi)
    return jsonify(
        {
            "ok": True,
            "year": year,
            "series": series,
            "men_interpolated": mi,
            "women_interpolated": wi,
            "men": df_to_json_rows(men_ui),
            "women": df_to_json_rows(women_ui),
        }
    )


@app.post("/api/optimize")
def api_optimize():
    data = request.get_json(force=True, silent=True) or {}
    try:
        budget_m = int(data.get("budget_m", 1000))
        budget_w = int(data.get("budget_w", 1000))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid budgets."}), 400

    men_rows = data.get("men") or []
    women_rows = data.get("women") or []
    if not isinstance(men_rows, list) or not isinstance(women_rows, list):
        return jsonify({"ok": False, "error": "Expected men and women arrays."}), 400

    def _opt(rows: list, budget: int, label: str):
        if not rows:
            return {"label": label, "combinations": []}
        df = pd.DataFrame(rows)
        for col in ("crew", "cost", "return"):
            if col not in df.columns:
                return {"label": label, "error": f"Missing column {col}", "combinations": []}
        df = df.dropna(subset=["cost"])
        if df.empty:
            return {"label": label, "combinations": []}
        df = df.reset_index(drop=True)
        n = len(df)
        costs = df["cost"].astype(int).values
        returns = df["return"].astype(int).values
        crews = df["crew"]
        if "popularity" in df.columns:
            pop = pd.to_numeric(df["popularity"], errors="coerce").to_numpy(dtype=float)
        else:
            pop = np.full(n, np.nan)
        if "division" in df.columns:
            divisions = df["division"].fillna("").astype(str).to_numpy()
        else:
            divisions = np.array([""] * n, dtype=object)
        if "bumps_position" in df.columns:
            bumps_positions = pd.to_numeric(df["bumps_position"], errors="coerce").to_numpy(dtype=float)
        else:
            bumps_positions = np.full(n, np.nan)
        if "row_over" in df.columns:
            row_overs = pd.to_numeric(df["row_over"], errors="coerce").to_numpy(dtype=float)
        else:
            row_overs = np.full(n, np.nan)
        combos = run_optimization(
            crews,
            costs,
            returns,
            budget,
            top_n=10,
            popularity=pop,
            divisions=divisions,
            bumps_positions=bumps_positions,
            row_overs=row_overs,
        )
        return {"label": label, "combinations": combos}

    return jsonify(
        {
            "ok": True,
            "men": _opt(men_rows, budget_m, "Men's boat"),
            "women": _opt(women_rows, budget_w, "Women's boat"),
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("FANTASY_BUMPS_PORT", "5050"))
    app.run(host="127.0.0.1", port=port, debug=False)
