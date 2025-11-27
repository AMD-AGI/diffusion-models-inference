import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import argparse
import pathlib
import pandas
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

"""
run with: python analyze_per_metric_regression.py --input-path results.csv --output-directory . --filter_n 5
"""


def parse_args():
    parser = argparse.ArgumentParser(description="Search and micro-benchmark ROCm/aiter")
    parser.add_argument("--input-path", type=str, required=True, help="Path to input data")
    parser.add_argument("--output-directory", type=str, default="results", help="Directory to write outputs")
    parser.add_argument("--filter_n", type=int, default=None, help="Filter last n commits")

    return parser.parse_args()

args = parse_args()
input_path = pathlib.Path(args.input_path)
output_directory = pathlib.Path(args.output_directory)
filter_n = args.filter_n

# read inputs
data = pandas.read_csv(input_path)
if filter_n is not None:
    list_of_commits = list(data['commit'].unique())[-filter_n:]
    data = data[data['commit'].isin(list_of_commits)]

df = data.copy()
df['config'] = df['seqlen_q'].astype(str) + '|' + df['seqlen_kv'].astype(str)
df['workload'] = df['tag']
df['runtime_ms'] = df['avg_time_ms']
df['throughput'] = df['throughput']
df['mae'] = df['mean_abs_diff_vs_sdpa']

# 1) Sequence per (workload, config)
df['seq'] = df.groupby(['workload','config']).cumcount()

# 2) % change vs previous within each (workload, config)
for col in ['runtime_ms','throughput','mae']:
    df[f'{col}_pct_prev'] = (
        df.groupby(['workload','config'], group_keys=False)[col]
          .apply(lambda s: s.pct_change()*100.0)
    )

# 3) Regression rules (tune thresholds)
thr = {'runtime_ms': +3.0, 'throughput': -5.0, 'mae': +1.0}  # % vs previous
def is_reg(col, d):
    if np.isnan(d): return False
    return (d <= thr[col]) if col=='throughput' else (d >= thr[col])

df['reg_runtime'] = df['runtime_ms_pct_prev'].apply(lambda d: is_reg('runtime_ms', d))
df['reg_thr']     = df['throughput_pct_prev'].apply(lambda d: is_reg('throughput', d))
df['reg_mae']     = df['mae_pct_prev'].apply(lambda d: is_reg('mae', d))
df['any_reg']     = df[['reg_runtime','reg_thr','reg_mae']].any(axis=1)

# 3) Improvement rules (tune thresholds)
thr = {'runtime_ms': -3.0, 'throughput': +10.0, 'mae': -1.0}  # % vs previous
def is_imp(col, d):
    if np.isnan(d): return False
    return (d >= thr[col]) if col=='throughput' else (d <= thr[col])

df['imp_runtime'] = df['runtime_ms_pct_prev'].apply(lambda d: is_imp('runtime_ms', d))
df['imp_thr']     = df['throughput_pct_prev'].apply(lambda d: is_imp('throughput', d))
df['imp_mae']     = df['mae_pct_prev'].apply(lambda d: is_imp('mae', d))
df['any_imp']     = df[['imp_runtime','imp_thr','imp_mae']].any(axis=1)

# 4) Per-workload: Scoreboard (latest vs previous for each config)
def status_row(r):
    icons = []
    icons.append('🔴' if r['reg_runtime'] else ('🟢' if r['imp_runtime'] else '🟡'))
    icons.append('🔴' if r['reg_thr'] else ('🟢' if r['imp_thr'] else '🟡'))
    icons.append('🔴' if r['reg_mae'] else ('🟢' if r['imp_mae'] else '🟡'))
    return ''.join(icons)

scoreboards = {}
for wl, g in df.sort_values(['workload','config','seq']).groupby('workload'):
    latest = g.groupby('config', as_index=False).tail(len(g)).copy()
    latest['status'] = latest.apply(status_row, axis=1)
    scoreboards[wl] = latest[['config','commit',
                              'runtime_ms','runtime_ms_pct_prev',
                              'throughput','throughput_pct_prev',
                              'mae','mae_pct_prev','status']]

# Store scoreboards in a text file
scoreboard_path = output_directory / "scoreboards.txt"
with open(scoreboard_path, 'w') as f:
    for wl, tbl in scoreboards.items():
        f.write(f"\n=== {wl}: Scoreboard (latest vs previous) ===\n")
        f.write(tbl.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
        f.write("\n")
print(f"\nScoreboards saved to: {scoreboard_path}")

# Normalized for trends (first commit per (workload,config) = 1.0)
for col in ['runtime_ms','throughput','mae']:
    base = df.groupby(['workload','config'])[col].transform('first')
    df[f'{col}_norm_first'] = df[col] / base

# Helper: compact delta string
def fmt_pct(v, good='lower'):
    if pd.isna(v): return '—'
    good_move = (v < 0) if good=='lower' else (v > 0)
    arrow = '↓' if good_move else '↑'
    return f"{v:+.2f}% {arrow}"

figs = []
# ---------- 1) Animated frontier (commit-by-commit across ALL workloads) ----------
#fig_frontier_anim = px.scatter(
#    df, x='mae', y='throughput',
#    color='workload', symbol='config',
#    animation_frame='commit', animation_group='config',
#    hover_data=['workload','config','commit','runtime_ms','throughput','mae'],
#    title='Frontier over commits — All workloads'
#)
#fig_frontier_anim.update_xaxes(title='MAE (lower is better)')
#fig_frontier_anim.update_yaxes(title='Throughput (higher is better)')
#figs.append(fig_frontier_anim)
# TODO: This plot is not good, add plot from analyze.py instead

# ---------- 2) All-series trends ----------
long_all = pd.melt(
    df[['workload','config','commit',
        'runtime_ms_norm_first','throughput_norm_first','mae_norm_first']],
    id_vars=['workload','config','commit'],
    var_name='metric', value_name='norm_value'
).replace({'runtime_ms_norm_first':'runtime (↓ better)',
           'throughput_norm_first':'throughput (↑ better)',
           'mae_norm_first':'MAE (↓ better)'})

fig_trend_all = px.line(
    long_all, x='commit', y='norm_value',
    color='workload', line_group='config', hover_data=['config'],
    facet_row='metric', markers=True,
    title='Trends (normalized to first commit) — All workloads together'
)
for ax in fig_trend_all.select_yaxes():
    fig_trend_all.add_hline(y=1.0, line_dash='dash')
figs.append(fig_trend_all)

# ---------- 3) Global regression heatmap ----------
df['wl_cfg'] = df['workload'] + ' | ' + df['config']
mat_all = (df.pivot_table(index='commit', columns='wl_cfg', values='any_reg', aggfunc='max')
             .reindex(index=(df['commit'].unique()))
             .astype(float))
fig_heat_all = px.imshow(
    mat_all.T, aspect='auto',
    title='Global Regression Heatmap (1 = regression)',
    labels=dict(x="Commit", y="Workload | Config", color="Reg")
)
figs.append(fig_heat_all)

# ---------- 4) Per-commit overview ----------
MAE_BUDGET = None  # set e.g. 0.02 to filter by quality; None = no filter

seq_data = {}
for seq_val, g in df.groupby('seq'):
    snap = g.copy()
    if MAE_BUDGET is not None:
        snap = snap[snap['mae'] <= MAE_BUDGET]
        if snap.empty:
            snap = g  # fallback: show all

    overall = (snap.sort_values(['throughput', 'mae', 'runtime_ms'],
                                ascending=[False, True, True])
                    [['workload','config','commit','throughput','mae','runtime_ms']]
                    .head(10))
    overall.insert(0, 'rank', range(1, len(overall)+1))

    # presentation tweaks
    def fmt_num(x, kind):
        if kind == 'thr': return f"{x:,.2f}"
        if kind == 'mae': return f"{x:.6f}"
        if kind == 'run': return f"{x:.3f}"
        return str(x)

    overall_disp = overall.copy()

    overall_disp['commit'] = overall_disp['commit'].astype(str).str.slice(0, 12)

    overall_disp = overall_disp.rename(columns={
        'rank':'#','workload':'workload','config':'config','commit':'commit',
        'throughput':'thr','mae':'mae','runtime_ms':'run_s'
    })

    seq_data[seq_val] = {
        "overall_cols": overall_disp.columns.tolist(),
        "overall_vals": [overall_disp[c].astype(str).tolist() for c in overall_disp.columns],
        "title":        f"",
        "subtitle":     f"Overview" + (f" (MAE≤{MAE_BUDGET})" if MAE_BUDGET is not None else "")
    }

# --- Single figure with dropdown to switch commit -------------------------------
first_seq = sorted(seq_data)[0]
cw_overall = [40, 160, 160, 160, 110, 100, 100]
cw_perwl   = [160, 160, 160, 110, 100, 100]

fig_tab = make_subplots(
    rows=2, cols=1, specs=[[{"type":"table"}],[{"type":"table"}]],
    row_heights=[0.72, 0.28], vertical_spacing=0.08,
    subplot_titles=(seq_data[first_seq]["subtitle"], "Placeholder?")
)

fig_tab.add_trace(go.Table(
    columnwidth=cw_overall,
    header=dict(
        values=[f"<b>{c}</b>" for c in seq_data[first_seq]["overall_cols"]],
        align='left', height=28, fill_color='#f0f0f0', font=dict(size=12)
    ),
    cells=dict(
        values=seq_data[first_seq]["overall_vals"],
        align='left', height=24, font=dict(size=12)
    )
), row=1, col=1)

# Dropdown buttons (one per seq)
buttons = []
for seq_val in sorted(seq_data):
    d = seq_data[seq_val]
    commit_id = d["overall_vals"][3][0]  # first commit in overall table
    buttons.append(dict(
        label=f"commit {commit_id[:12]}",
        method="update",
        args=[
            # Update both table traces' cells
            {
                "cells": [
                    dict(values=d["overall_vals"]),  # trace 0
                ]
            },
            # Update layout title and subplot subtitle (annotation[0])
            {
                "title.text": d["title"],
                "annotations[0].text": d["subtitle"],
            }
        ]
    ))

fig_tab.update_layout(
    title=seq_data[first_seq]["title"],
    title_x=0,
    autosize=False,
    width=1400, height=700,
    margin=dict(l=20, r=20, t=90, b=20),
    updatemenus=[dict(
        type="dropdown",
        direction="down",
        x=1.0, xanchor="right",
        y=1.15, yanchor="top",
        buttons=buttons,
        showactive=True
    )]
)

figs.append(fig_tab)

# Clear the file before writing
(output_directory / "figures.html").write_text("")

for fig in figs:
    with open(output_directory / "figures.html", 'a') as f:
        f.write(fig.to_html(full_html=False, include_plotlyjs='cdn'))

# 8) Optional: changelog lines per workload (commit→commit deltas)
def fmt(v): 
    if pd.isna(v): return '—'
    lower = (v<0)
    return f"{v:+.2f}% {'↓' if lower else '↑'}"

rows = []
for (wl, cfg), g in df.groupby(['workload','config']):
    for i in range(1, len(g)):
        r = g.iloc[i]
        rows.append({
            'workload': wl, 'config': cfg,
            'from': g.iloc[i-1]['commit'], 'to': r['commit'],
            'runtime': fmt(r['runtime_ms_pct_prev']),
            'thr':     fmt(r['throughput_pct_prev']),
            'mae':     fmt(r['mae_pct_prev']),
            'flags':   'REG' if r[['reg_runtime','reg_thr','reg_mae']].any() else ''
        })
changelog = pd.DataFrame(rows)
changelog_path = output_directory / "changelog.txt"
changelog.to_csv(changelog_path, index=False, sep='\t')
print(f"\nChangelog saved to: {changelog_path}")
