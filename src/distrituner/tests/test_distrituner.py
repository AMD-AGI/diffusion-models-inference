import unittest
import tempfile
from pathlib import Path

from distrituner import Task, distritune


class TestDistritune(unittest.TestCase):
    """Unit tests for the distritune function."""

    def test_all_tasks_succeed(self):
        """Test that all tasks complete successfully when they all return exit code 0."""
        tasks = [
            Task(command="exit 0"),
            Task(command="exit 0"),
            Task(command="exit 0"),
        ]
        worker_envs = [
            {"WORKER_ID": "0"},
            {"WORKER_ID": "1"},
        ]
        
        # Should not raise any exception
        distritune(tasks, worker_envs)

    def test_task_fails_with_nonzero_exit_code(self):
        """Test that distritune exits early when a task fails with non-zero exit code."""
        tasks = [
            Task(command="exit 1"),  # This should fail
            Task(command="sleep 1 && exit 0"),  # This should be cancelled
            Task(command="sleep 1 && exit 0"),  # This should be cancelled
        ]
        worker_envs = [
            {"WORKER_ID": "0"},
            {"WORKER_ID": "1"},
        ]
        
        # Should raise RuntimeError due to non-zero exit code
        with self.assertRaises(RuntimeError) as context:
            distritune(tasks, worker_envs)
        
        self.assertIn("failed with exit code", str(context.exception))

    def test_task_fails_early_cancels_remaining(self):
        """Test that when one task fails, remaining tasks are cancelled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            
            # Create tasks where first fails quickly, others would take long time
            tasks = [
                Task(command="exit 1", log_file=output_dir / "task0.json"),
                Task(command="sleep 1 && exit 0", log_file=output_dir / "task1.json"),
                Task(command="sleep 1 && exit 0", log_file=output_dir / "task2.json"),
            ]
            worker_envs = [
                {"WORKER_ID": "0"},
                {"WORKER_ID": "1"},
            ]
            
            # Should raise RuntimeError quickly (not wait 5 seconds)
            with self.assertRaises(RuntimeError):
                distritune(tasks, worker_envs)
            
            # Only the first task should have completed and written output
            self.assertTrue((output_dir / "task0.json").exists())
            # The other tasks should not have completed (or been cancelled before writing)
            # Note: They might not exist or might exist depending on timing

    def test_successful_tasks_with_output_logging(self):
        """Test that successful tasks write their output logs correctly."""
        import json
        
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            
            tasks = [
                Task(command="echo 'hello world'", log_file=output_dir / "task0.json"),
                Task(command="echo 'test output'", log_file=output_dir / "task1.json"),
            ]
            worker_envs = [
                {"WORKER_ID": "0"},
                {"WORKER_ID": "1"},
            ]
            
            distritune(tasks, worker_envs)
            
            # Check that both output files exist
            self.assertTrue((output_dir / "task0.json").exists())
            self.assertTrue((output_dir / "task1.json").exists())
            
            # Verify the content of one output file
            with open(output_dir / "task0.json") as f:
                data = json.load(f)
                self.assertEqual(data["returncode"], 0)
                self.assertIn("hello world", data["stdout"])
                self.assertEqual(data["stderr"], "")
                self.assertEqual(data["command"], "echo 'hello world'")

    def test_task_fails_continue_on_failure(self):
        """Test that when stop_on_failure=False, all tasks run despite failures."""
        import json
        
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            
            tasks = [
                Task(command="exit 1", log_file=output_dir / "task0.json"),
                Task(command="exit 0", log_file=output_dir / "task1.json"),
                Task(command="exit 2", log_file=output_dir / "task2.json"),
            ]
            worker_envs = [
                {"WORKER_ID": "0"},
                {"WORKER_ID": "1"},
            ]
            
            # Should raise RuntimeError at the end with summary of failures
            with self.assertRaises(RuntimeError) as context:
                distritune(tasks, worker_envs, stop_on_failure=False)
            
            # Should mention that tasks failed
            self.assertIn("tasks failed", str(context.exception))
            
            # All tasks should have completed
            self.assertTrue((output_dir / "task0.json").exists())
            self.assertTrue((output_dir / "task1.json").exists())
            self.assertTrue((output_dir / "task2.json").exists())
            
            # Verify the return codes
            with open(output_dir / "task0.json") as f:
                self.assertEqual(json.load(f)["returncode"], 1)
            with open(output_dir / "task1.json") as f:
                self.assertEqual(json.load(f)["returncode"], 0)
            with open(output_dir / "task2.json") as f:
                self.assertEqual(json.load(f)["returncode"], 2)

    def test_results_returned_in_submission_order(self):
        """Results align with task order even when shorter tasks finish first."""
        tasks = [
            Task(command="sleep 0.2 && echo slow"),
            Task(command="echo fast"),
        ]
        worker_envs = [
            {"WORKER_ID": "0"},
            {"WORKER_ID": "1"},
        ]

        results = distritune(tasks, worker_envs)

        self.assertIn("slow", results[0].stdout)
        self.assertIn("fast", results[1].stdout)

    def test_stderr_logging(self):
        """Test that both stdout and stderr are captured in the log file."""
        import json
        
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            
            # Create tasks that write to both stdout and stderr
            tasks = [
                Task(
                    command="echo 'stdout message' && echo 'stderr message' >&2",
                    log_file=output_dir / "task0.json",
                ),
                Task(
                    command="echo 'only stdout'",
                    log_file=output_dir / "task1.json",
                ),
            ]
            worker_envs = [
                {"WORKER_ID": "0"},
                {"WORKER_ID": "1"},
            ]
            
            distritune(tasks, worker_envs)
            
            # Check that log files exist
            self.assertTrue((output_dir / "task0.json").exists())
            self.assertTrue((output_dir / "task1.json").exists())
            
            # Verify task0 has both stdout and stderr
            with open(output_dir / "task0.json") as f:
                data = json.load(f)
                self.assertEqual(data["returncode"], 0)
                self.assertIn("stdout message", data["stdout"])
                self.assertIn("stderr message", data["stderr"])
                self.assertNotIn("stderr message", data["stdout"])
            
            # Verify task1 has only stdout
            with open(output_dir / "task1.json") as f:
                data = json.load(f)
                self.assertIn("only stdout", data["stdout"])
                self.assertEqual(data["stderr"], "")


if __name__ == "__main__":
    unittest.main()
