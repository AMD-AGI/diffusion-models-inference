"""
This is a parser of a single `timings.json` file produced by benchmarking an xDiT model,
to be used with the `benchstats` statistical analysis tool.

Typical use for comparing performance of the same model run differently (assumes running
in a typical xDiT container with the current directory visible in the countainer as
/app/_my_src/diffusion-models-inference/bulk_bench/benchstats_parsers;
It's advisable to have a single directory with all the sources on the host machine
filesystem and simply mount it to containers as /app/_my_src):

```bash
pip install benchstats  # if not installed yet

# make a directory to store benchmark results if not exist
export MYDIR=/app/_my_src/res
mkdir $MYDIR

#save model name to simplify execution later
export MYMODEL="flux.usp_2k"

# measure performance of the model with default settings, 28 iterations (the first 3
# are ignored as warmup)
python /app/.ci/run.py --name "$MYMODEL" --results-directory "$MYDIR/1" \
    --override-args-json '{ "num_iterations": 28 }' /app/.ci/benchmark_configs/*.yaml

# change anything you need in the code and run to produce another set of results
python /app/.ci/run.py --name "$MYMODEL" --results-directory "$MYDIR/2" \
    --override-args-json '{ "num_iterations": 28 }' /app/.ci/benchmark_configs/*.yaml

# now run the statistical comparison:
benchstats "$MYDIR/1/$MYMODEL" "$MYDIR/2/$MYMODEL" \
    --files_parser /app/_my_src/diffusion-models-inference/bulk_bench/benchstats_parsers/SingleModel.py \
    --always_show_pvalues --sample_stats 0 50 100 --multiline --export_to ./res.svg
```
"""

import json
import numpy as np
import os

from benchstats.common import ParserBase


class SingleModel(ParserBase):
    def __init__(self, fpath, filter, metrics, debug_log=None) -> None:
        assert filter is None, "Filter is not supported for xDiT SingleModel parser"
        assert metrics == ["real_time"], (
            "Only default metrics are supported for xDiT SingleModel parser"
        )

        if os.path.isdir(fpath):
            fpath = os.path.join(fpath, "timings.json")

        self.bmname = os.path.basename(os.path.dirname(fpath))
        with open(fpath, "r") as f:
            self.data = json.load(f)
            if len(self.data) > 2:
                print(
                    "Dropping another 2 first elements from the JSON to ignore first 3 iterations as warmup"
                )
                self.data = self.data[2:]

    def getStats(self) -> dict[str, dict[str, np.ndarray]]:
        return {self.bmname: {"real_time": self.data}}
