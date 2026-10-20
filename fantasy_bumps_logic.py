"""Shared fetch + optimize logic for the Fantasy Bumps optimizer.

Game rules (https://fantasybumps.org.uk/guide-rules/), per seat, evaluated each day:
  * value change = price of the new river position - price of the old one
  * cash payout  = 7% of the crew's value for rowing over, plus 14% per place gained
  * headship row over pays 17.5% (the head crew cannot bump)
So a crew at river rank r gaining k places pays
  price[r-k] - price[r] + round(price[r] * (0.07 + 0.14 * k)),
which reproduces the site's published "+1" payout for every crew (k = 1).
"""

from __future__ import annotations

import datetime as _dt
import re
from html import unescape

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = "https://fantasybumps.org.uk"

# Site buy ladder for an 85-crew event (Costs.rtf, river order). Only a last-resort fallback:
# events with a different number of crews (e.g. Torpids, 73) use a different ladder, so prices
# come from the JSON API, or are rebuilt from the HTML's bumped-down deltas.
REFERENCE_CRAB_LADDER = (
    300,
    290,
    281,
    273,
    265,
    257,
    249,
    242,
    235,
    228,
    221,
    214,
    208,
    202,
    196,
    190,
    184,
    178,
    173,
    168,
    163,
    158,
    153,
    148,
    143,
    139,
    135,
    131,
    127,
    123,
    119,
    115,
    111,
    108,
    105,
    102,
    99,
    96,
    93,
    90,
    87,
    84,
    81,
    78,
    76,
    74,
    72,
    70,
    68,
    66,
    64,
    62,
    60,
    58,
    56,
    54,
    52,
    50,
    48,
    46,
    44,
    43,
    42,
    41,
    40,
    39,
    38,
    37,
    36,
    35,
    34,
    33,
    32,
    31,
    30,
    29,
    28,
    27,
    26,
    25,
    24,
    23,
    22,
    21,
    20,
)


assert len(REFERENCE_CRAB_LADDER) == 85, "REFERENCE_CRAB_LADDER must stay in sync with Costs.rtf (85 crews)"


def concluded_costs_from_returns(df: pd.DataFrame) -> pd.Series:
    """Last-resort prices by row order using REFERENCE_CRAB_LADDER (exact only for 85-crew events)."""

    def _floor_price(i: int) -> int:
        if i < len(REFERENCE_CRAB_LADDER):
            return int(REFERENCE_CRAB_LADDER[i])
        return int(REFERENCE_CRAB_LADDER[-1])

    n = len(df)
    vals = [_floor_price(i) for i in range(n)]
    return pd.Series(vals, dtype="int64")


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


def parse_market_page(html: str) -> tuple[pd.DataFrame, bool, bool]:
    """Returns (df, prices_were_estimated, event_concluded).

    prices_were_estimated is True when costs were filled by the model (event over, or no buy
    buttons in HTML — e.g. open market without sign-in where the site omits public prices).
    """
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
            if buy is None:
                buy = item.find("a", class_=lambda c: c and "btn-buy" in c)
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
        return df, False, concluded
    df["rank"] = np.arange(1, len(df) + 1)
    df["cost"] = df["cost"].astype("Int64")
    scraped_any = bool(df["cost"].notna().any())
    if scraped_any and not concluded:
        return df, False, False
    # No buy prices in the markup (concluded event, or anonymous fetch). Prices are fixed by river
    # position, and "bumped down" is exactly price[r+1] - price[r], so rebuild the ladder from the
    # 300-crab headship price. Fall back to the 85-step reference ladder if that looks wrong.
    df["cost"] = _prices_from_bumped_down(df).astype("Int64")
    return df, True, concluded


HEADSHIP_PRICE = 300
FOOTSHIP_PRICE = 20


def _prices_from_bumped_down(df: pd.DataFrame) -> pd.Series:
    deltas = df["bumped_down"].fillna(0).astype(int).to_numpy()
    prices = [HEADSHIP_PRICE]
    for d in deltas[:-1]:
        prices.append(prices[-1] + int(d))
    ok = (
        len(prices) > 1
        and prices[-1] == FOOTSHIP_PRICE
        and all(a > b for a, b in zip(prices, prices[1:]))
    )
    if ok:
        return pd.Series(prices, dtype="int64")
    return concluded_costs_from_returns(df)


def _division_label(gender: str, division) -> str:
    g = "Men's" if str(gender).lower().startswith("m") else "Women's"
    return f"{g} Division {division}"


def parse_market_json(data: dict, gender: str) -> tuple[pd.DataFrame, bool, bool]:
    """Parse the site's JSON market (``/<event>/<gender>/?format=json``).

    Returns (df, prices_were_estimated, event_concluded). The JSON carries real prices even
    for concluded events, so prices are never estimated here.
    """
    day = data.get("day") or {}
    concluded = str(day.get("name") or "").strip().lower() == "finish"
    rows: list[dict] = []
    for e in sorted(data.get("start_order") or [], key=lambda x: int(x.get("rank") or 0)):
        crew = e.get("crew") or {}
        payouts = e.get("payouts") or {}
        code = str(crew.get("club_code") or "").strip()
        name = f"{crew.get('club_name', '')} {crew.get('gender', '')}{crew.get('rank', '')}".strip()
        pop = e.get("popularity")
        rows.append(
            {
                "division": _division_label(gender, e.get("division")),
                "club_css": f"club-{code}" if code else "club-other",
                "bumps_position": int(e["bungline"]) if e.get("bungline") is not None else None,
                "crew": name,
                "return": int(payouts.get("1", 0)),
                "row_over": int(payouts.get("0", 0)),
                "bumped_down": int(payouts.get("-1", 0)),
                "popularity": float(pop) if pop is not None else float("nan"),
                "cost": int(e["price"]) if e.get("price") is not None else None,
                "rank": int(e.get("rank") or 0),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df, False, concluded
    df["cost"] = df["cost"].astype("Int64")
    return df, False, concluded


def fetch_event(series: str, year: int, gender: str) -> tuple[pd.DataFrame, bool, bool]:
    url = f"{BASE}/{series}{year}/{gender}/"
    try:
        r = requests.get(
            url, params={"format": "json"}, headers={"Accept": "application/json"}, timeout=30
        )
        r.raise_for_status()
        df, est, concluded = parse_market_json(r.json(), gender)
        if not df.empty:
            return df, est, concluded
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return parse_market_page(r.text)


_SERIES_CODE = {"eights": "E", "torpids": "T", "mays": "M", "lents": "L"}


def latest_year_for_series(series: str, min_y: int = 2020, max_y: int | None = None) -> int | None:
    # Preferred: the site's event list.
    try:
        r = requests.get(f"{BASE}/events/", params={"format": "json"},
                         headers={"Accept": "application/json"}, timeout=15)
        r.raise_for_status()
        code = _SERIES_CODE.get(series)
        years = [
            int(e["year"])
            for e in (r.json().get("events") or [])
            if (e.get("tag") or "").startswith(series) or (code and e.get("series") == code)
        ]
        if years:
            return max(years)
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass
    # Fallback: probe from next year downwards (not from 2100 — that is ~150 requests).
    if max_y is None:
        max_y = _dt.date.today().year + 1
    for y in range(max_y, min_y - 1, -1):
        u = f"{BASE}/{series}{y}/men/"
        try:
            h = requests.head(u, timeout=10, allow_redirects=True)
            if h.status_code == 200:
                return y
            if h.status_code == 404:
                continue
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


SEATS = 9


def top_k_seat_combinations(costs, returns, budget: int, top_n: int = 10, seats: int = SEATS):
    """Best ``top_n`` multisets of exactly ``seats`` crews (repeats allowed) with total cost <= budget.

    Exact dynamic programme over (seats filled, exact crab cost), keeping the top_n returns per
    cell. Each multiset has a unique decomposition (last crew index used), so cells never hold
    duplicates and the global top_n is exact. Ties are broken by lower total cost.
    Replaces the old brute-force enumeration, which grows as C(n+8, 9) and runs out of memory
    once ~20 crews are ticked.

    Returns (list of index tuples, list of total returns, list of total costs).
    """
    costs = np.asarray(costs, dtype=np.int64)
    returns = np.asarray(returns, dtype=np.int64)
    n = len(costs)
    if n == 0 or top_n <= 0 or budget < 0 or (costs < 0).any():
        return [], [], []
    cap = int(min(budget, seats * int(costs.max())))
    K = int(top_n)
    NEG = np.iinfo(np.int64).min // 4
    # val[s, c, k]: k-th best return using s seats at exact cost c;
    # sel[s, c, k, :s]: the crew indices of that multiset.
    val = np.full((seats + 1, cap + 1, K), NEG, dtype=np.int64)
    sel = np.full((seats + 1, cap + 1, K, seats), -1, dtype=np.int32)
    val[0, 0, 0] = 0
    for i in range(n):
        ci, ri = int(costs[i]), int(returns[i])
        if ci > cap:
            continue
        for s in range(1, seats + 1):
            # Add one more seat of crew i to every (s-1)-seat multiset (which may already use i).
            ext_v = np.full((cap + 1, K), NEG, dtype=np.int64)
            src_v = val[s - 1, : cap + 1 - ci, :]
            ext_v[ci:, :] = np.where(src_v > NEG, src_v + ri, NEG)
            ext_s = np.full((cap + 1, K, seats), -1, dtype=np.int32)
            ext_s[ci:, :, :] = sel[s - 1, : cap + 1 - ci, :, :]
            ext_s[ci:, :, s - 1] = i
            cand_v = np.concatenate([val[s], ext_v], axis=1)
            cand_s = np.concatenate([sel[s], ext_s], axis=1)
            order = np.argsort(-cand_v, axis=1, kind="stable")[:, :K]
            val[s] = np.take_along_axis(cand_v, order, axis=1)
            sel[s] = np.take_along_axis(cand_s, order[:, :, None], axis=1)
    final = val[seats]  # (cap+1, K)
    cs, ks = np.nonzero(final > NEG)
    flat = sorted(zip(final[cs, ks].tolist(), cs.tolist(), ks.tolist()), key=lambda t: (-t[0], t[1]))
    picks, totals, total_costs = [], [], []
    for tot, c, k in flat[:K]:
        picks.append(tuple(sorted(int(x) for x in sel[seats, c, k])))
        totals.append(int(tot))
        total_costs.append(int(c))
    return picks, totals, total_costs


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
    picks, totals, total_costs = top_k_seat_combinations(costs, returns, budget, top_n=top_n)
    if not picks:
        return []
    out = []
    for rank, (comb, tot_r, tot_c) in enumerate(zip(picks, totals, total_costs)):
        detail_records = []
        for ix in comb:
            rec: dict = {"crew": str(crews.iloc[ix]), "cost": int(costs[ix]), "return": int(returns[ix])}
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
        out.append(
            {"rank": rank + 1, "total_return": int(tot_r), "total_cost": int(tot_c), "detail": detail_records}
        )
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
            "rank": int(r["rank"]) if "rank" in r and pd.notna(r.get("rank")) else None,
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
