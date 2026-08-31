from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass
class FlowState:
    lane: str
    completed_stages: list[str]
    flow_doc_path: str | None


def flow_state_to_json(state: FlowState) -> str:
    return json.dumps(asdict(state), sort_keys=True)
