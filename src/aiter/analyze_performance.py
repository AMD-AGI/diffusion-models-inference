import argparse
import pathlib

import pandas
import plotly

"""
run with python analyze_performance.py --input-path results.csv --output-directory .
"""


def parse_args():
    parser = argparse.ArgumentParser(description="Search and micro-benchmark ROCm/aiter")
    parser.add_argument("--input-path", type=str, required=True, help="Path to input data")
    parser.add_argument("--output-directory", type=str, default="results", help="Directory to write outputs")

    return parser.parse_args()


def summarize(data: pandas.DataFrame) -> pandas.DataFrame:
    def _avg_run_time(items: pandas.DataFrame) -> pandas.Series:
        total_run_time_s = (items['ncalls'] * items["avg_time_ms"] / 1000.0).sum()
        throughput_avg = (items["throughput"] * items["ncalls"]).sum() / items["ncalls"].sum()
        avg_error = items["mean_abs_diff_vs_sdpa"].mean()
        return pandas.Series({
            "run time [s]": total_run_time_s,
            "thpt_avg": throughput_avg,
            "avg error": avg_error
        })

    data = data.groupby(by=["commit", "tag"], sort=False).apply(_avg_run_time).reset_index(drop=False)

    return data


def plot(data: pandas.DataFrame, output_path: pathlib.Path):
    # Filter out entries where avg error > 0.1
    data = data[data["avg error"] <= 0.1]
    
    fig = plotly.graph_objects.Figure()

    tags = list(data["tag"].unique())

    for tag in tags:
        subset = data[data["tag"] == tag]
        fig.add_trace(
            plotly.graph_objects.Bar(   
                y=subset["commit"],
                x=subset["run time [s]"],
                name=tag,
                orientation='h'
            )
        )

    # Add dropdown menu for filtering number of commits
    unique_commits = data["commit"].unique()
    
    commit_buttons = []
    # Add "All" option
    commit_buttons.append(
        dict(
            label="All commits",
            method="update",
            args=[
                {"y": [[data[data["tag"] == tag]["commit"].values for tag in tags][i] for i in range(len(tags))],
                 "x": [[data[data["tag"] == tag]["run time [s]"].values for tag in tags][i] for i in range(len(tags))]}
            ]
        )
    )
    
    # Add options for different numbers of commits
    for num in [5, 10, 20, 50]:
        if len(unique_commits) >= num:
            commit_buttons.append(
                dict(
                    label=f"Last {num} commits",
                    method="update",
                    args=[
                        {"y": [[data[(data["tag"] == tag) & (data["commit"].isin(unique_commits[-num:]))]["commit"].values for tag in tags][i] for i in range(len(tags))],
                         "x": [[data[(data["tag"] == tag) & (data["commit"].isin(unique_commits[-num:]))]["run time [s]"].values for tag in tags][i] for i in range(len(tags))]}
                    ]
                )
            )
    
    # Add dropdown menu for metric selection
    metric_buttons = [
        dict(
            label="Run time [s]",
            method="update",
            args=[{"x": [[data[data["tag"] == tag]["run time [s]"].values for tag in tags][i] for i in range(len(tags))]}]
        ),
        dict(
            label="Throughput avg",
            method="update",
            args=[{"x": [[data[data["tag"] == tag]["thpt_avg"].values for tag in tags][i] for i in range(len(tags))]}]
        )
    ]
    
    fig.update_layout(
        updatemenus=[
            dict(
                buttons=commit_buttons,
                direction="down",
                showactive=True,
                x=0.17,
                xanchor="left",
                y=1.15,
                yanchor="top"
            ),
            dict(
                buttons=metric_buttons,
                direction="down",
                showactive=True,
                x=0.17,
                xanchor="left",
                y=1.05,
                yanchor="top"
            )
        ],
    )

    html_string = fig.to_html(include_plotlyjs='cdn')

    with open(output_path.resolve(), 'w') as f:
        f.write(html_string)


def main():
    args = parse_args()
    input_path = pathlib.Path(args.input_path)

    # read inputs
    data = pandas.read_csv(input_path)

    # prepare outputs
    output_directory = pathlib.Path(args.output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)

    # summarize
    summary = summarize(data)
    summary.to_csv(output_directory / "summary.csv", index=False)

    # plot
    plot(summary, output_directory / "plot.html")


if __name__ == "__main__":
    main()
