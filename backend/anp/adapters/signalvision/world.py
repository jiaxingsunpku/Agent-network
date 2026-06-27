"""SignalVision world-registration transition helpers.

The existing SV adapter/executor still publish traffic lifecycle and heartbeat
records so the v1 command loop keeps working.  This module adds WorldClient
instances for the same processes so they also appear in ``anp.world.agent.*``
with per-key channel declarations for the unified ``/world`` catalog.
"""

from __future__ import annotations

from anp.contracts import Channel, TrafficTopics
from anp.world import WorldClient

from .config import SignalVisionAdapterConfig, SignalVisionExecConfig
from .executor import EXEC_CAPABILITIES, EXEC_COMMAND_TYPES
from .service import SV_CAPABILITIES, SV_COMMAND_TYPES


def _intersection_keys(junction_map: dict[str, str]) -> list[str]:
    """Return unique ANP intersection ids while preserving config order."""

    return list(dict.fromkeys(junction_map.values()))


def signalvision_adapter_produces(config: SignalVisionAdapterConfig) -> list[Channel]:
    """Perception channel covered by the SV adapter."""

    return [Channel(topic=TrafficTopics.OBSERVATION, keys=_intersection_keys(config.junction_map))]


def signalvision_executor_consumes(config: SignalVisionExecConfig) -> list[Channel]:
    """Command channel covered by the SV executor."""

    return [Channel(topic=TrafficTopics.COMMAND, keys=_intersection_keys(config.junction_map))]


def signalvision_executor_produces(config: SignalVisionExecConfig) -> list[Channel]:
    """Ack channel produced by the SV executor; acks are command-level, not per-intersection."""

    return [Channel(topic=TrafficTopics.ACK, keys=[])]


def build_signalvision_adapter_world_client(
    config: SignalVisionAdapterConfig,
    *,
    bootstrap: str | None = None,
    producer=None,
) -> WorldClient:
    """Build the SV perception WorldClient.  The caller decides when to register."""

    return WorldClient(
        config.agent_id,
        agent_type=config.agent_type,
        capabilities=SV_CAPABILITIES,
        command_types=SV_COMMAND_TYPES,
        produces=signalvision_adapter_produces(config),
        consumes=[],
        bootstrap=bootstrap,
        producer=producer,
    )


def build_signalvision_exec_world_client(
    config: SignalVisionExecConfig,
    *,
    bootstrap: str | None = None,
    producer=None,
) -> WorldClient:
    """Build the SV executor WorldClient.  The caller decides when to register."""

    return WorldClient(
        config.agent_id,
        agent_type=config.agent_type,
        capabilities=EXEC_CAPABILITIES,
        command_types=EXEC_COMMAND_TYPES,
        produces=signalvision_executor_produces(config),
        consumes=signalvision_executor_consumes(config),
        bootstrap=bootstrap,
        producer=producer,
    )
