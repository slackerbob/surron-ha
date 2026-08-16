"""Shared data model passed from the coordinator to entities."""

from __future__ import annotations

from dataclasses import dataclass, field

from .telemetry import Telemetry


@dataclass
class SurronData:
	"""The latest poll result for one bike.

	``raw`` always holds every command's raw hex response (stateCode -> hex), so nothing is
	lost even before the decoder is complete. ``telemetry`` carries decoded values once the
	frame layout is known; ``soc_estimate`` is the ebmx-ha-style voltage-based estimate.
	"""

	telemetry: Telemetry
	raw: dict[str, str] = field(default_factory=dict)
	soc_estimate: float | None = None
