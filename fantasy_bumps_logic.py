"""Shared fetch + optimize logic (matches notebook behaviour)."""

from __future__ import annotations

import re
from html import unescape
from itertools import combinations_with_replacement

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = "https://fantasybumps.org.uk"


def interpolated_costs(n_crews: int) -> np.ndarray:
    """When the event has concluded: head 300, foot 20, linear by row order."""
    if n_crews <= 0:
        return np.array([], dtype=int)
    if n_crews == 1:
        return np.array([300], dtype=int)
    r = np.arange(n_crews, dtype=float)
    return np.round(300 - 280 * r / (n_crews - 1)).astype(int)


def parse_signed_int(val: str | None, default: int = 0) -> int:
    if val is None:
        return default
    s = str(val).strip().replace("\u2212", "-")
    if s.startswith("+"):
        s = s[1:]
    try:
        return int(s)
    except ValueError:
        return default


def _club_class_from_avatar(avatar) -> tuple[str, int | None]:
    """Return (club-css-class, bumps position in division) from span.avatar."""
    if avatar is None:
        return "club-other", None
    classes = avatar.get("class") or []
    club = "club-other"
    for c in classes:
        if isinstance(c, str) and c.startswith("club-"):
            club = c
            break
    pos = None
    try:
        pos = int(avatar.get_text(strip=True))
    except ValueError:
        pos = None
    return club, pos


def parse_market_page(html: str) -> tuple[pd.DataFrame, bool]:
    """Returns (df, costs_were_interpolated). Walks list-groups like the live site (divisions + blades)."""
    soup = BeautifulSoup(html, "html.parser")
    concluded = "this event has concluded" in html.lower()
    rows: list[dict] = []
    current_division = ""

    for group in soup.select("div.list-group"):
        for item in group.find_all("div", recursive=False):
            cls = item.get("class") or []
            if "list-group-item" not in cls:
                continue
            if "list-group-item-dark" in cls:
                h5 = item.find("h5")
                if h5:
                    current_division = unescape(h5.get_text(strip=True))
                continue
            if "market-row" not in cls:
                continue
            btn = item.find("button", class_=lambda c: c and "payout" in c)
            if not btn:
                continue
            name_el = item.find("div", class_="flex-grow-1")
            if name_el is None:
                continue
            name = unescape(name_el.get_text(strip=True))
            bump_up = parse_signed_int(btn.get("data-bump-up"))
            row_over = parse_signed_int(btn.get("data-row-over"))
            bumped_dn = parse_signed_int(btn.get("data-bumped-down"))
            pop = btn.get("data-popularity")
            try:
                popularity = float(pop) if pop is not None else float("nan")
            except ValueError:
                popularity = float("nan")
            buy = item.find("button", class_=lambda c: c and "btn-buy" in c)
            price: int | None = None
            if buy is not None:
                for attr in ("data-cost", "data-price"):
                    v = buy.get(attr)
                    if v is not None and str(v).strip() != "":
                        price = parse_signed_int(v)
                        break
                if price is None:
                    t = buy.get_text()
                    m = re.search(r"(\d+)", t)
                    if m:
                        price = int(m.group(1))
            avatar = item.find("span", class_=lambda c: c and "avatar" in c)
            club_css, bumps_pos = _club_class_from_avatar(avatar)
            rows.append(
                {
                    "division": current_division,
                    "club_css": club_css,
                    "bumps_position": bumps_pos,
                    "crew": name,
                    "return": bump_up,
                    "row_over": row_over,
                    "bumped_down": bumped_dn,
                    "popularity": popularity,
                    "cost": price,
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df, False
    df["cost"] = df["cost"].astype("Int64")
    if concluded:
        df["cost"] = interpolated_costs(len(df))
        return df, True
    return df, False


def fetch_event(series: str, year: int, gender: str) -> tuple[pd.DataFrame, bool]:
    url = f"{BASE}/{series}{year}/{gender}/"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return parse_market_page(r.text)


def latest_year_for_series(series: str, min_y: int = 2020, max_y: int = 2035) -> int | None:
    for y in range(max_y, min_y - 1, -1):
        u = f"{BASE}/{series}{y}/men/"
        try:
            h = requests.head(u, timeout=10, allow_redirects=True)
            if h.status_code == 200:
                return y
            g = requests.get(u, timeout=10, allow_redirects=True)
            if g.status_code == 200 and "Fantasy Bumps" in g.text:
                return y
        except requests.RequestException:
            continue
    return None


def crews_for_ui(df: pd.DataFrame, interpolated: bool) -> pd.DataFrame:
    """Same rows as notebook checkboxes: all crews if interpolated, else only priced."""
    if df.empty:
        return df
    if interpolated:
        return df.reset_index(drop=True)
    return df.dropna(subset=["cost"]).reset_index(drop=True)


def run_optimization(
    crews: pd.Series,
    costs: np.ndarray,
    returns: np.ndarray,
    budget: int,
    top_n: int = 10,
    *,
    popularity: np.ndarray | None = None,
    divisions: np.ndarray | None = None,
    bumps_positions: np.ndarray | None = None,
    row_overs: np.ndarray | None = None,
):
    n = len(crews)
    if n == 0:
        return []
    if popularity is None:
        popularity = np.full(n, np.nan)
    if divisions is None:
        divisions = np.array([""] * n, dtype=object)
    if bumps_positions is None:
        bumps_positions = np.full(n, np.nan)
    if row_overs is None:
        row_overs = np.full(n, np.nan)
    all_combinations = np.array(list(combinations_with_replacement(range(n), 9)))
    combination_costs = costs[all_combinations].sum(axis=1)
    combination_returns = returns[all_combinations].sum(axis=1)
    valid_mask = combination_costs <= budget
    valid_combinations = all_combinations[valid_mask]
    valid_returns = combination_returns[valid_mask]
    if len(valid_returns) == 0:
        return []
    top_indices = np.argsort(valid_returns)[-top_n:][::-1]
    out = []
    for rank, top_idx in enumerate(top_indices):
        comb = valid_combinations[top_idx]
        tot_r = int(valid_returns[top_idx])
        detail_records = []
        for ix in comb:
            rec: dict = {"crew": str(crews.iloc[ix]), "return": int(returns[ix])}
            pop = popularity[ix]
            if pd.isna(pop):
                rec["popularity"] = None
            else:
                rec["popularity"] = round(float(pop), 4)
            div = divisions[ix]
            rec["division"] = "" if pd.isna(div) or div is None else str(div)
            bp = bumps_positions[ix]
            if pd.isna(bp):
                rec["bumps_position"] = None
            else:
                try:
                    rec["bumps_position"] = int(bp)
                except (TypeError, ValueError):
                    rec["bumps_position"] = None
            ro = row_overs[ix]
            if pd.isna(ro):
                rec["row_over"] = None
            else:
                try:
                    rec["row_over"] = int(ro)
                except (TypeError, ValueError):
                    rec["row_over"] = None
            detail_records.append(rec)
        out.append({"rank": rank + 1, "total_return": tot_r, "detail": detail_records})
    return out


def df_to_json_rows(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    out = []
    for _, r in df.iterrows():
        ro = r.get("row_over")
        bd = r.get("bumped_down")
        row = {
            "division": str(r.get("division") or ""),
            "club_css": str(r.get("club_css") or "club-other"),
            "crew": str(r["crew"]),
            "cost": int(r["cost"]) if pd.notna(r["cost"]) else None,
            "return": int(r["return"]),
            "row_over": int(ro) if pd.notna(ro) else None,
            "bumped_down": int(bd) if pd.notna(bd) else None,
        }
        bp = r.get("bumps_position")
        if pd.notna(bp) and bp is not None:
            try:
                row["bumps_position"] = int(bp)
            except (TypeError, ValueError):
                row["bumps_position"] = None
        else:
            row["bumps_position"] = None
        if pd.notna(r.get("popularity")):
            row["popularity"] = round(float(r["popularity"]), 4)
        else:
            row["popularity"] = None
        out.append(row)
    return out
