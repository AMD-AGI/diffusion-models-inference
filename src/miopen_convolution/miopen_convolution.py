# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Compatibility import for the MIOpen convolution problem model."""

from miopen_tuning.problem import MIOpenConvolution, find_db_key_to_perf_db_key

__all__ = ["MIOpenConvolution", "find_db_key_to_perf_db_key"]
