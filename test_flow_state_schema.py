import json
import unittest

from flow_state_schema import FlowState, flow_state_to_json


class FlowStateToJsonTests(unittest.TestCase):
    def test_json_shape_for_populated_state(self) -> None:
        state = FlowState(
            lane="standard",
            completed_stages=["understand", "plan"],
            flow_doc_path="/tmp/flow.md",
        )

        result = flow_state_to_json(state)
        decoded = json.loads(result)

        self.assertEqual(
            decoded,
            {
                "completed_stages": ["understand", "plan"],
                "flow_doc_path": "/tmp/flow.md",
                "lane": "standard",
            },
        )

    def test_flow_doc_path_none(self) -> None:
        state = FlowState(lane="express", completed_stages=[], flow_doc_path=None)

        decoded = json.loads(flow_state_to_json(state))

        self.assertIsNone(decoded["flow_doc_path"])
        self.assertEqual(decoded["completed_stages"], [])
        self.assertEqual(decoded["lane"], "express")

    def test_keys_are_sorted(self) -> None:
        state = FlowState(
            lane="heavy",
            completed_stages=["understand"],
            flow_doc_path=None,
        )

        result = flow_state_to_json(state)

        self.assertEqual(
            result,
            '{"completed_stages": ["understand"], "flow_doc_path": null, "lane": "heavy"}',
        )


if __name__ == "__main__":
    unittest.main()
