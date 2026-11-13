# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import unittest

from miopen_tuning.problem import MIOpenConvolution, find_db_key_to_perf_db_key


class ProblemKeyTest(unittest.TestCase):
    CASES = [
        (
            "MIOpenDriver convbfp16 -n 1 -c 16 -H 16 -W 16 -k 16 -y 3 -x 3 -p 1 -q 1 -u 1 -v 1 -l 1 -j 1 -m conv -g 1 -F 1 -t 1",
            "16-16-16-3x3-16-16-16-1-1x1-1x1-1x1-0-NCHW-BF16-F",
            "2x16x16x16x1x3x3x1x16x1x1x1x0x1x1x0x1x1x0x0x1xNCHWxBF16xF",
        ),
        (
            "MIOpenDriver convbfp16 -n 1 -c 16 --in_d 4 -H 16 -W 16 -k 16 --fil_d 3 -y 3 -x 3 --pad_d 1 -p 1 -q 1 --conv_stride_d 1 -u 1 -v 1 --dilation_d 1 -l 1 -j 1 --spatial_dim 3 -m conv -g 1 -F 1 -t 1",
            "16-4-16-16-3x3x3-16-4-16-16-1-1x1x1-1x1x1-1x1x1-0-NCDHW-BF16-F",
            "3x16x16x16x4x3x3x3x16x1x1x1x1x1x1x1x1x1x1x0x1xNCDHWxBF16xF",
        ),
        (
            "MIOpenDriver convfp16 -n 1 -c 32 -H 17 -W 19 -k 32 -y 3 -x 3 -p 1 -q 1 -u 2 -v 2 -l 1 -j 1 -m conv -g 2 -F 1 -t 1",
            "32-17-19-3x3-32-9-10-1-1x1-2x2-1x1-0-NCHW-FP16-F_g2",
            "2x32x17x19x1x3x3x1x32x1x1x1x0x2x2x0x1x1x0x0x2xNCHWxFP16xF",
        ),
    ]

    def test_observed_miopen_keys(self):
        for command, find_key, perf_key in self.CASES:
            with self.subTest(command=command):
                problem = MIOpenConvolution.from_miopendriver_command(command)
                self.assertEqual(problem.to_find_db_key(), find_key)
                self.assertEqual(problem.to_perf_db_key(), perf_key)
                self.assertEqual(find_db_key_to_perf_db_key(find_key), perf_key)

    def test_backward_find_key_round_trips(self):
        key = "1024-1-207-1x9-512-1-1035-2-0x2-1x5-1x1-0-NCHW-FP32-B"
        self.assertEqual(MIOpenConvolution.from_db_key(key).to_find_db_key(), key)
        self.assertEqual(
            find_db_key_to_perf_db_key(key),
            "2x1024x1x207x1x1x9x1x512x2x0x2x0x1x5x0x1x1x0x0x1xNCHWxFP32xB",
        )


if __name__ == "__main__":
    unittest.main()
