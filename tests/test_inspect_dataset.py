from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ontoanno.agent_router import _intent_from_tool, _tool_schemas
from ontoanno.worker_runtime import AVAILABLE_WORKERS, run_inspect_dataset_worker


class InspectDatasetWorkerTest(unittest.TestCase):
    def test_returns_configured_dataset_summary_without_running_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            seurat_path = Path(tmp_dir) / "dataset.rds"
            seurat_path.touch()
            orchestrator = SimpleNamespace(
                run_id="demo-20260719T120000+0000",
                config={
                    "project": {"name": "Demo", "work_dir": "/work/Demo"},
                    "inputs": {
                        "seurat_rds": str(seurat_path),
                        "reference_labels_csv": "/data/demo/labels.csv",
                    },
                    "annotation": {
                        "species": "human",
                        "tissue_name": "human pancreatic tumor",
                        "preprocess": True,
                        "parent_res": [0.1, 0.3],
                        "sub_res": [0.1, 0.2],
                        "min_cell_count": 3000,
                    },
                },
            )

            outputs, result = run_inspect_dataset_worker(orchestrator)

        summary = outputs["summary"]
        self.assertEqual(summary["species"], "human")
        self.assertEqual(summary["tissue_name"], "human pancreatic tumor")
        self.assertTrue(summary["seurat_rds_exists"])
        self.assertEqual(summary["parent_resolutions"], [0.1, 0.3])
        self.assertEqual(summary["subcluster_resolutions"], [0.1, 0.2])
        self.assertEqual(result["worker"], "inspect_dataset")
        self.assertEqual(result["status"], "completed")

    def test_worker_and_router_tool_are_registered(self) -> None:
        self.assertIn("inspect_dataset", AVAILABLE_WORKERS)
        tool_names = [item["function"]["name"] for item in _tool_schemas()]
        self.assertIn("inspect_dataset", tool_names)
        intent = _intent_from_tool(
            "inspect_dataset",
            {"reason": "Describe the dataset"},
            user_request="Give me some basic information about the dataset",
            session={},
        )
        self.assertEqual(intent["intent_type"], "inspect_dataset")


if __name__ == "__main__":
    unittest.main()
