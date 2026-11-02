"""How much of what Steam players buy do they ever play, and who drives playtime?

Decision rules, fixed before running:
- Make library activation a priority if 25%+ of purchased titles are never played,
  after removing likely add-ons. DLC can't log play time of its own and the file
  doesn't label it. This adjustment was added after seeing DLC top the raw ranking.
- Make high-playtime players a priority segment if the top 10% hold 50%+ of hours.
"""
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gamestats as gs  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

OUT = os.path.join(HERE, "..", "out")
LIB_BINS = [0, 1, 5, 20, 100, np.inf]
LIB_LABELS = ["1", "2–5", "6–20", "21–100", "100+"]
MIN_BUYERS = 200          # owners needed before a game gets its own rate
EARLY_HOURS = 2           # Steam's refund window, used only as a marker
ADDON_MIN_OWNERS = 20     # with this many owners and almost no play, a title looks like an add-on
ADDON_NEVER_PLAYED = .90


def load():
    d = pd.read_csv(gs.data_path("steam_200k.csv"), header=None, names=["user", "game", "action", "value", "x"])
    assert d.shape[0] == 200000 and d["user"].nunique() == 12393 and d["game"].nunique() == 5155, "unexpected file"
    assert set(d["action"]) == {"purchase", "play"}
    return d


def main():
    gs.style()
    raw = load()
    d = raw.drop_duplicates(["user", "game", "action"])
    buy = d[d["action"] == "purchase"][["user", "game"]]
    play = d[d["action"] == "play"][["user", "game", "value"]].rename(columns={"value": "hours"})
    t = buy.merge(play, on=["user", "game"], how="left")
    t["played"] = t["hours"].notna()
    facts = {"checks": {"rows": len(raw), "users": raw["user"].nunique(), "games": raw["game"].nunique(),
                        "duplicate_rows_removed": len(raw) - len(d), "user_titles": len(t),
                        "play_rows_without_purchase": int(len(play.merge(buy, on=["user", "game"], how="left", indicator=True).query("_merge=='left_only'")))}}

    # Overall activation with a bootstrap by user, since one user's titles aren't independent.
    u = t.groupby("user").agg(owned=("game", "size"), played=("played", "sum"), hours=("hours", "sum"))
    rng = np.random.default_rng(42)
    draws = []
    arr = u[["owned", "played"]].to_numpy()
    for _ in range(5000):
        s = arr[rng.integers(0, len(arr), len(arr))]
        draws.append(1 - s[:, 1].sum() / s[:, 0].sum())
    never = 1 - t["played"].mean()
    facts["activation"] = {"never_played_share": never, "ci_low": float(np.percentile(draws, 2.5)), "ci_high": float(np.percentile(draws, 97.5)),
                           "priority": bool(never >= .25), "median_library": float(u["owned"].median()),
                           "users_with_all_played": float((u["played"] == u["owned"]).mean()),
                           "users_with_no_play": float((u["played"] == 0).mean())}

    # Titles nobody plays are mostly DLC, which can't log play time. Remove likely add-ons.
    per_game = t.groupby("game")["played"].agg(["size", "sum"])
    zero = per_game.index[per_game["sum"] == 0]
    addon = per_game.index[(per_game["sum"] == 0) | ((per_game["size"] >= ADDON_MIN_OWNERS) & (1 - per_game["sum"] / per_game["size"] >= ADDON_NEVER_PLAYED))]
    no_zero = t[~t["game"].isin(zero)]
    core = t[~t["game"].isin(addon)]
    uc = core.groupby("user").agg(owned=("game", "size"), played=("played", "sum"))
    arr = uc.to_numpy()
    draws = [1 - s[:, 1].sum() / s[:, 0].sum() for s in (arr[rng.integers(0, len(arr), len(arr))] for _ in range(5000))]
    adj = 1 - core["played"].mean()
    facts["activation"]["priority"] = bool(adj >= .25)
    facts["adjusted"] = {"titles_nobody_played": len(zero), "user_titles_removed_nobody_played": len(t) - len(no_zero),
                         "never_played_without_those": 1 - no_zero["played"].mean(), "likely_addons": len(addon),
                         "user_titles_removed": len(t) - len(core), "share_of_user_titles_removed": 1 - len(core) / len(t),
                         "never_played_share": adj, "ci_low": float(np.percentile(draws, 2.5)), "ci_high": float(np.percentile(draws, 97.5))}

    # Second look: titles almost everyone plays may be free games logged only once played.
    near_all = per_game.index[(per_game["size"] >= ADDON_MIN_OWNERS) & (1 - per_game["sum"] / per_game["size"] <= .02)]
    trimmed = core[~core["game"].isin(near_all)]
    facts["adjusted"]["titles_almost_always_played"] = len(near_all)
    facts["adjusted"]["never_played_also_without_those"] = 1 - trimmed["played"].mean()
    one = t[t["user"].isin(u.index[u["owned"] == 1])]["game"].value_counts(normalize=True)
    facts["one_title_players"] = {"users": int((u["owned"] == 1).sum()), "dota_2": float(one["Dota 2"]), "team_fortress_2": float(one["Team Fortress 2"])}
    facts["segments"] = {}
    for name, g in [("2_to_20", uc[(uc["owned"] >= 2) & (uc["owned"] <= 20)]), ("21_plus", uc[uc["owned"] > 20])]:
        facts["segments"][name] = {"users": len(g), "share_of_users": len(g) / len(uc), "share_of_titles": float(g["owned"].sum() / uc["owned"].sum()),
                                   "share_of_unplayed": float((g["owned"] - g["played"]).sum() / (uc["owned"] - uc["played"]).sum()),
                                   "never_played": float(1 - g["played"].sum() / g["owned"].sum()), "median_unplayed_per_user": float((g["owned"] - g["played"]).median())}

    # Library size: bigger libraries, more untouched titles (descriptive).
    u["lib"] = pd.cut(u["owned"], LIB_BINS, labels=LIB_LABELS)
    libs = []
    for label in LIB_LABELS:
        g = u[u["lib"] == label]
        libs.append({"library": label, "users": len(g), "titles": int(g["owned"].sum()), "share_of_titles": g["owned"].sum() / u["owned"].sum(),
                     "never_played": 1 - g["played"].sum() / g["owned"].sum(), "median_hours": float(g["hours"].median()),
                     "never_played_adjusted": 1 - core[core["user"].isin(g.index)]["played"].mean()})
    facts["by_library"] = libs
    rho, p = stats.spearmanr(u["owned"], u["played"] / u["owned"])
    facts["library_rho"] = {"spearman_rho": float(rho), "p_value": float(p)}
    big = uc[uc["owned"] > 20]
    facts["big_libraries"] = {"users": len(big), "share_of_users": len(big) / len(uc),
                              "share_of_never_played": float((big["owned"] - big["played"]).sum() / (uc["owned"] - uc["played"]).sum())}

    # Hours are concentrated, by player and by game.
    players = u[u["hours"] > 0]
    gh = t.groupby("game")["hours"].sum()
    facts["concentration"] = {"players_with_hours": len(players), "gini_players": gs.gini(players["hours"]),
                              "top_10pct_players_share": gs.top_share(players["hours"], .10), "top_1pct_players_share": gs.top_share(players["hours"], .01),
                              "priority": bool(gs.top_share(players["hours"], .10) >= .5),
                              "top_1pct_games_share": gs.top_share(gh[gh > 0], .01), "top_game": gh.idxmax(), "top_game_share": float(gh.max() / gh.sum())}
    trimmed_hours = t[~t["game"].isin(near_all)].groupby("user")["hours"].sum()
    trimmed_hours = trimmed_hours[trimmed_hours > 0]
    facts["concentration"]["top_10pct_players_share_without_almost_always_played"] = gs.top_share(trimmed_hours, .10)
    facts["concentration"]["three_biggest_online_games_share"] = float(gh[["Dota 2", "Counter-Strike Global Offensive", "Team Fortress 2"]].sum() / gh.sum())
    h = t.loc[t["played"], "hours"]
    facts["playtime"] = {"titles_played": len(h), "median_hours": gs.bootstrap_stat(h.values), "under_2_hours": float((h < EARLY_HOURS).mean())}

    # Per game, with enough owners. Bonferroni-free: the ranking is descriptive, and Wilson CIs are shown.
    games = t.groupby("game").agg(buyers=("user", "size"), played=("played", "sum"), median_hours=("hours", "median")).query("buyers >= @MIN_BUYERS")
    rows = []
    for g, r in games.iterrows():
        k = int(r["buyers"] - r["played"])
        rows.append({"game": g, "buyers": int(r["buyers"]), **{a: b for a, b in gs.rate(k, int(r["buyers"])).items() if a not in ("n",)}, "median_hours": float(r["median_hours"])})
    rows = sorted(rows, key=lambda x: -x["rate"])
    pd.DataFrame(rows).to_csv(os.path.join(OUT, "games_never_played.csv"), index=False)
    facts["games"] = {"games_with_enough_buyers": len(rows), "highest_never_played": rows[:8], "lowest_never_played": rows[-8:]}

    fig, ax = plt.subplots(figsize=(10, 3.8))
    x = np.arange(len(libs))
    for off, key, col, name in [(-.2, "never_played", gs.SERIES[0], "all titles"), (.2, "never_played_adjusted", gs.SERIES[1], "likely add-ons removed")]:
        vals = [100 * r[key] for r in libs]
        ax.bar(x + off, vals, color=col, width=.36, label=name)
        for i, v in enumerate(vals):
            ax.text(i + off, v + 1.5, f"{v:.0f}%", ha="center", fontsize=9.5)
    ax.set_xticks(x, ["1 title (mostly free games)" if r["library"] == "1" else f"{r['library']} titles" for r in libs]); ax.tick_params(axis="x", colors=gs.INK2, length=0)
    ax.legend(loc="upper left", ncols=2)
    ax.set_ylim(0, 100); ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0f}%"))
    ax.set_title("Share of owned titles never played, by library size")
    gs.save_fig(fig, OUT, "01_never_played_by_library.png")

    show = rows[:8] + rows[-8:]
    fig, ax = plt.subplots(figsize=(10, 5.4))
    y = np.arange(len(show))[::-1]
    for yy, r in zip(y, show):
        ax.plot([100 * r["ci_low"], 100 * r["ci_high"]], [yy, yy], color=gs.NEUTRAL, zorder=2)
        ax.scatter(100 * r["rate"], yy, s=48, color=gs.NEUTRAL, edgecolor=gs.SURFACE, linewidth=2, zorder=3)
    ax.set_yticks(y, [r["game"] for r in show]); ax.tick_params(axis="y", colors=gs.INK2)
    ax.grid(axis="y", visible=False); ax.grid(axis="x", visible=True); ax.set_xlim(-3, 103)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_xlabel("owners who never played (95% CI)")
    ax.set_title(f"Highest and lowest never-played rates ({MIN_BUYERS}+ owners; the top group is mostly add-ons)")
    gs.save_fig(fig, OUT, "02_never_played_by_game.png")
    gs.save_results(facts, OUT)
    return facts


if __name__ == "__main__":
    import json
    print(json.dumps(main(), indent=1, default=float))
