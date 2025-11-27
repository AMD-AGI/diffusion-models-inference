import argparse
import pathlib

import pandas


def parse_args():
    parser = argparse.ArgumentParser(description="Find and concatenate attention signatures")
    parser.add_argument("--root-path", type=str, default=".", help="Path to repository root")
    parser.add_argument("--output-path", type=str, required=True, help="Path to a CSV file to write combined signatures")

    return parser.parse_args()


def main():
    args = parse_args()
    root_path = pathlib.Path(args.root_path)

    file_names = root_path.glob("src/*/aiter/signatures.csv")

    data = []
    for file_name in file_names:
        data.append(
            pandas.read_csv(file_name)
        )
    data = pandas.concat(data)

    output_path = pathlib.Path(args.output_path)
    data.to_csv(output_path, index=False)


if __name__ == "__main__":
    main()
