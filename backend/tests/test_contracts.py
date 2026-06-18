"""契约回归测试：envelope 往返、分区键、校验严格性、schema 与模型一致性。

运行：``backend/.venv/bin/python -m pytest`` （cwd = backend/）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from anp import contracts as c

REPO_ROOT = Path(__file__).resolve().parents[2]


def _obs() -> c.ObservationPayload:
    return c.ObservationPayload(
        intersection_id="gg-xiongchu-minzu",
        approaches=[
            c.Approach(direction=c.Direction.NORTH, vehicle_count=12, halting_count=5, mean_speed_mps=8.3),
            c.Approach(direction=c.Direction.SOUTH, vehicle_count=7, halting_count=2, mean_speed_mps=11.0),
        ],
    )


def test_observation_roundtrip_via_json():
    env = c.observation_envelope(agent_id="traffic-virtual-001", payload=_obs(), event_ts="2026-06-18T08:00:00.000Z")
    back = c.Envelope.model_validate_json(json.dumps(env.to_wire()))
    payload = c.parse_payload(back)
    assert isinstance(payload, c.ObservationPayload)
    assert payload.intersection_id == "gg-xiongchu-minzu"
    assert back.time.event_ts == "2026-06-18T08:00:00.000Z"
    assert back.schema_version == c.SCHEMA_VERSION


def test_partition_key_rules():
    obs_env = c.observation_envelope(agent_id="traffic-virtual-001", payload=_obs())
    # 感知层按 agent_id 分区
    assert c.partition_key(obs_env) == "traffic-virtual-001"

    status = c.IntersectionStatusPayload(
        intersection_id="gg-xiongchu-minzu",
        window=c.StatusWindow(start="2026-06-18T08:00:00.000Z", end="2026-06-18T08:00:10.000Z", size_sec=10, sample_count=5),
        queue_length_m=35.0, flow_veh_h=180.0, mean_speed_kmh=29.9, mean_delay_sec=41.2,
        congestion_level=c.CongestionLevel.CONGESTED, congestion_index=0.62, approaches=[],
    )
    st_env = c.status_envelope(agent_id="traffic-system-001", payload=status)
    # 状态层按被聚合实体 object_id（intersection_id）分区
    assert c.partition_key(st_env) == "gg-xiongchu-minzu"


def test_command_requires_target_and_expires():
    cmd = c.command_envelope(
        source=c.Source(system=c.SourceSystem.PLATFORM, agent_id="traffic-gateway-001"),
        target_agent_id="traffic-virtual-001",
        payload=c.CommandPayload(command_id="cmd-1", command_type=c.CommandType.SET_SIGNAL_PLAN, params={"duration_s": 25}),
        expires_at=c.expires_at_iso(30),
    )
    assert cmd.target.agent_id == "traffic-virtual-001"
    assert cmd.time.expires_at is not None
    assert c.partition_key(cmd) == "traffic-gateway-001"


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        c.ObservationPayload.model_validate(
            {"intersection_id": "x", "approaches": [], "bogus": 1}
        )


def test_empty_approaches_rejected():
    with pytest.raises(ValidationError):
        c.ObservationPayload(intersection_id="x", approaches=[])


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        c.Quality(confidence=1.5)


@pytest.mark.parametrize(
    "filename,model",
    [
        ("envelope.schema.json", c.Envelope),
        ("observation.schema.json", c.ObservationPayload),
        ("status.intersection.schema.json", c.IntersectionStatusPayload),
        ("command.schema.json", c.CommandPayload),
        ("ack.schema.json", c.AckPayload),
    ],
)
def test_schema_files_in_sync_with_models(filename, model):
    """schemas/*.json 必须与模型一致（改契约后需重跑 gen_schemas.py）。"""

    committed = json.loads((REPO_ROOT / "schemas" / filename).read_text(encoding="utf-8"))
    generated = model.model_json_schema()
    # gen_schemas.py 额外注入的两个字段不参与对比
    committed.pop("$schema", None)
    committed.pop("title", None)
    generated.pop("title", None)
    assert committed == generated
