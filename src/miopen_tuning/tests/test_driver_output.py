# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import unittest

from miopen_tuning.driver_output import parse_line, solver_name, summarize_events


class DriverOutputTest(unittest.TestCase):
    def events(self, text):
        events = []
        for line_number, line in enumerate(text.splitlines(), 1):
            events.extend(parse_line(line, source="stdout.log", line_number=line_number))
        return events

    def test_solver_name_removes_id_and_kernel_config(self):
        self.assertEqual(solver_name("98/ConvMlirIgemmFwd:config"), "ConvMlirIgemmFwd")

    def test_parses_driver_solution_and_timing(self):
        events = self.events(
            "MIOpen Forward Conv. Algorithm: 5, Solution: 98/ConvMlirIgemmFwd\n"
            "GPU Kernel Time Forward Conv. Elapsed: 0.125 ms\n"
        )

        self.assertEqual(events[0]["event"], "solution")
        self.assertEqual(events[0]["solver"], "ConvMlirIgemmFwd")
        self.assertEqual(events[0]["line"], 1)
        self.assertEqual(events[1], {"event": "timing", "direction": "F", "time_ms": 0.125, "source": "stdout.log", "line": 2})

    def test_parses_performance_json(self):
        events = self.events(
            '{"performance":{"direction":"forward","algorithm":5,'
            '"solution":"ConvAsmImplicitGemmGTCDynamicFwdXdlopsNHWC",'
            '"db_key":"find-key","perf_db_key":"perf-key",'
            '"results":{"average_time_ms":0.25}}}\n'
        )

        self.assertEqual(events[0]["event"], "performance_record")
        self.assertEqual(events[0]["solver"], "ConvAsmImplicitGemmGTCDynamicFwdXdlopsNHWC")
        self.assertEqual(events[0]["db_key"], "find-key")
        self.assertEqual(events[0]["perf_db_key"], "perf-key")
        self.assertEqual(events[0]["average_time_ms"], 0.25)
        summary = summarize_events(events)
        self.assertEqual(summary["db_keys"], ["find-key"])
        self.assertEqual(summary["perf_db_keys"], ["perf-key"])

    def test_parses_db_and_kernel_logs(self):
        events = self.events(
            "[FindSolutionImpl] ConvHipImplicitGemmGroupFwdXdlops\n"
            "[GetValues] key=ConvHipImplicitGemmGroupFwdXdlops:Kernel<1, 2>\n"
            "] ConvHipImplicitGemmGroupFwdXdlops: Candidate Selection selected: Kernel<1, 2>\n"
        )

        self.assertEqual([event["event"] for event in events], ["solver_considered", "db_record_loaded", "kernel_selected"])

    def test_summary_tracks_selection_errors_and_warnings(self):
        summary = summarize_events(
            self.events(
                "MIOpen Forward Conv. Algorithm: 5, Solution: 1/A\n"
                "MIOpen Forward Conv. Algorithm: 6, Solution: 2/B\n"
                "Warning: slow\n"
                "warning: argument unused during compilation: '-dD' [-Wunused-command-line-argument]\n"
                "MIOpen Error: failed\n"
            )
        )

        self.assertEqual(summary["selected_solver"], "B")
        self.assertEqual(summary["warnings"], 1)
        self.assertEqual(summary["compiler_warnings"], 1)
        self.assertEqual(summary["errors"], 1)


if __name__ == "__main__":
    unittest.main()
