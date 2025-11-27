import argparse
import importlib
import pathlib
import subprocess
import sys

import git
import pandas
import tqdm
import json
import pandas as pd
import os


def parse_args():
    parser = argparse.ArgumentParser(description="Search and micro-benchmark ROCm/aiter")
    parser.add_argument("--aiter-path", type=str, default="aiter", help="ROCm/aiter local path")
    parser.add_argument("--aiter-signatures", type=str, required=True, help="Path to CSV file containing benchmark signatures")
    parser.add_argument("--output-path", type=str, default="results.csv", help="Path to save CSV outputs")
    parser.add_argument("--token", type=str, required=True, help="Github token")
    parser.add_argument("--commit", type=str, required=True, help="ROCm/aiter commit SHA to start the search from")
    parser.add_argument("--verbose", action="store_true", help="Print detailed output")

    return parser.parse_args()


def clone(path: pathlib.Path, owner: str, repo:str, token: str, verbose: bool = False):
    url = f"https://github.com/{owner}/{repo}.git"

    if path.exists:
        output = subprocess.run(
            ["git", "reset", "--hard", "origin/main"],
            cwd=path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        if verbose:
            print(output.stdout.strip())
        return

    try:
        output = subprocess.run(
            ["git", "clone", url.replace("https://", f"https://{token}@"), path.resolve()],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        if verbose:
            print(output.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"Failed to clone {url}: {e}")
        sys.exit(1)


def reset(path: pathlib.Path, commit: str, verbose: bool = False):
    try:
        output = subprocess.run(
            ["git", "reset", "--hard", commit],
            cwd=path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        if verbose:
            print(output.stdout.strip())
        output = subprocess.run(
            ["git", "submodule", "sync"],
            cwd=path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        if verbose:
            print(output.stdout.strip())
        output = subprocess.run(
            ["git", "submodule", "update", "--init", "--recursive"],
            cwd=path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        if verbose:
            print(output.stdout.strip())
        output = subprocess.run(
            ["pip", "uninstall", "-y", "aiter"],
            cwd=path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        if verbose:
            print(output.stdout.strip())
        output = subprocess.run(
            ["rm -fr aiter/jit/*.so aiter/jit/build"],
            cwd=path,
            capture_output=True,
            shell=True,
            text=True,
            check=True
        )
        if verbose:
            print(output.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"Failed to reset: {e}")
        sys.exit(1)


def install(path: pathlib.Path, verbose: bool = False):
    try:
        output = subprocess.run(
            ["python", "setup.py", "develop"],
            cwd=path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        if verbose:
            print(output.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"Failed to install: {e}")
        sys.exit(1)


def check():
    try:
        output = subprocess.run(
            ["pip list | grep 'aiter'"],
            shell=True,
            capture_output=True,
            text=True,
            check=True
        )
        print(output.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"Failed to check: {e}")
        sys.exit(1)


def cleanup(path: pathlib.Path, verbose: bool = False):
    try:
        output = subprocess.run(
            ["pip", "uninstall", "-y", "aiter"],
            cwd=path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        if verbose:
            print(output.stdout.strip())
        output = subprocess.run(
            ["rm", "-rf", path.resolve()],
            cwd=path.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        if verbose:
            print(output.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"Failed to cleanup: {e}")
        sys.exit(1)

def apply_pybind11_patch(path: pathlib.Path, verbose: bool = False):
    try:
        output = subprocess.run(
            ["git", "apply", "/workspace/diffusion-models-inference/src/aiter/aiter_pybind11.patch"],
            cwd=path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        if verbose:
            print(output.stdout.strip())
    except subprocess.CalledProcessError as e:
        print(f"Failed to apply patch: {e}, with path: {path.resolve()}")
        raise subprocess.CalledProcessError(output.returncode, output.args)

def benchmark_worker_main():
    """Entry point for subprocess - reads from stdin and writes to stdout"""
    import json
    import io
    from benchmark import benchmark_attention
    
    sig_json = sys.stdin.read()
    signatures = pd.read_json(io.StringIO(sig_json), orient="records")
    r = benchmark_attention("aiter", signatures=signatures)
    
    print("===RESULT_START===")
    print(json.dumps(r.to_dict(orient='records')))
    print("===RESULT_END===")

def run_benchmark_subprocess(signatures: pd.DataFrame) -> pd.DataFrame:
    payload = signatures.to_json(orient="records")
    
    p = subprocess.run(
        [sys.executable, "-u", __file__, "--benchmark-worker"],
        input=payload, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONNOUSERSITE": "1"}
    )
    
    if p.returncode != 0:
        print(p.stdout, file=sys.stderr)
        raise subprocess.CalledProcessError(p.returncode, p.args, output=p.stdout)

    out = p.stdout
    start = out.find("===RESULT_START===") + len("===RESULT_START===")
    end = out.find("===RESULT_END===")
    json_str = out[start:end].strip()
    return pd.DataFrame(json.loads(json_str))

def main():
    # setup
    args = parse_args()

    owner = "ROCm"
    repo = "aiter"
    branch = "main"
    path = pathlib.Path(args.aiter_path)

    # read in attention signatures
    signatures = pandas.read_csv(args.aiter_signatures)

    print(f"Set CWD to {path.resolve()}")
    # clone
    print(f"Cloning {owner}/{repo} to {path.resolve()}")
    clone(path, owner, repo, args.token, args.verbose)

    # get commits
    repo = git.Repo(path.resolve())
    commits = list(repo.iter_commits(branch))
    
    # Find the starting point
    index = next((i for i, c in enumerate(commits) if c.hexsha.startswith(args.commit)), None)
    if index is None:
        raise ValueError(f"{args.commit} not found in {branch}")

    # benchmark
    print("Start benchmarking")
    data = []
    error_count = 0
    for commit in tqdm.tqdm(commits[index:: -1]):
        if args.verbose:
            print(f"Reset to commit {commit.hexsha}")
        reset(path, commit.hexsha, args.verbose)
        if args.verbose:
            print(f"Install")
        install(path, args.verbose)

        try:
            results = run_benchmark_subprocess(signatures)
        except subprocess.CalledProcessError as e:
            print(f"Failed to benchmark aiter: {e if args.verbose else ''}")
            print("Trying to apply pybind11 patch and re-build")
            try:
                apply_pybind11_patch(path)
                results = run_benchmark_subprocess(signatures)
            except Exception as e:
                print(f"Failed to benchmark aiter after patch: {e if args.verbose else ''}")
                print(f"Skipping commit {commit.hexsha}")
                error_count += 1
                if error_count > 5:
                    print("Too many errors, stopping the search.")
                    break
                continue

        if args.verbose:
            check()
            print(results)
        results["commit"] = commit.hexsha 
        data.append(results)
    data = pandas.concat(data, ignore_index=True)
    data.to_csv(args.output_path, index=False)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--benchmark-worker":
        benchmark_worker_main()
    else:
        main()
