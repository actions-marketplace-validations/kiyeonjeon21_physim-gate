"""Two hand-written CartPole controllers used by the Gymnasium example."""

from __future__ import annotations

from typing import Any


def pole_angle(observation: Any) -> int:
    """Push toward the side the pole leans to. Simple and fairly good."""
    return 1 if observation[2] > 0 else 0


def pole_angle_and_velocity(observation: Any) -> int:
    """Also uses angular velocity. Balances much longer."""
    return 1 if observation[2] + 0.5 * observation[3] > 0 else 0
