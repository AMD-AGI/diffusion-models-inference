## Attention signatures
The first step is to collect attention signatures. For instance, for Hunyuan, the attention signature would look something like this:


| tag | dtype | bs | seqlen_q | nheads_q | seqlen_kv | nheads_kv | headdim_qk | headdim_v | ncalls |
|-----|-------|----|---------|---------|-----------|-----------|-----------|-----------| -------|
| hunyuanvideo.720x1280_u8r1 | bfloat16 | 1 | 118936 | 3 | 118817 | 3 | 128 | 128 | 24000 |


The signatures are currently collected manually, but in the future, they should be collected automatically, stored under each workload, and collected with the `get_signatures.py` script and stored under a single csf file.

## Search
Once the signatures are collected, the search script can be used to go through a number of commits. The user simply specifies the start commit, and the search is conducted from the start commit to the latest main. To run the search script:
`python search.py --commit <commit hash to start the search from>  --token <git token> --path <path to local aiter repo> --aiter-signatures <path to signatures file>`

It is also possible to specify the following parameters: \
`--output-path <path where to store the results> (defaults to results.csv)` \
`--verbose`

Once the search script is done, the results will be stored in a csv file, where runtime, throughput, and mae vs sdpa has been recorded for each signature, for each workload in each commit.

## Analysis
If the numbers of commit in the search space is few, one could simply use the resulting csv from the search to identify potential regressions. However, we do also provide two analysis script, **still work in progress**, to help identifying regressions, improvements and also give a bit more hinesight in what is causing performance differences. 

The first script, `analyze_performance.py`, simply creates a bar plot for run time and throughput, which would clearly show performance regressions/improvements. Simply run it with: `python analyze_performance.py --input-path results.csv --output-directory .`, the script will output a html file and a csv file.

The second script, `analyze_per_metric_regression.py`, is looking att each metric for each signature, to determine if there is a regression or not. Giving more information about each workload as a whole. This script is run similarly to above: `python analyze_per_metric_regression.py --input-path results.csv --output-directory . --filter_n 5` but here the user can choose how many commits to use, by specifying `filter_n`. If this parameter is omitted, all commits are analuysed. The script outputs a html file, and 2 csv files. The first is flagging commits that causes a regression in each workload, and the second is also highlighting regressions, but for each metric individually for each workload. 