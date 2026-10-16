from __future__ import annotations

from pathlib import Path

import pytest

from physim_gate.config import Pairing, Statistic, load_config
from physim_gate.schema import SchemaError


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "gate.yaml"
    path.write_text(text)
    return path


def test_defaults_and_overrides(tmp_path: Path) -> None:
    config = load_config(
        write(
            tmp_path,
            """
version: 1
defaults: {min_samples: 20, statistic: median}
metrics:
  a: {max_absolute_degradation: 1}
  b: {max_relative_degradation: 0.1, min_samples: 5, pairing: unpaired, max_missing_fraction: 0}
""",
        )
    )
    a, b = config.resolve("a"), config.resolve("b")
    assert (a.min_samples, a.statistic, a.pairing) == (20, Statistic.MEDIAN, Pairing.AUTO)
    assert (b.min_samples, b.pairing, b.max_missing_fraction) == (5, Pairing.UNPAIRED, 0.0)
    assert a.confidence == 0.95
    assert config.policy.on_inconclusive.value == "fail"


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("version: 1\nmetrics: {a: {}}", "set max_absolute_degradation"),
        ("version: 2\nmetrics: {a: {max_absolute_degradation: 1}}", "version"),
        ("version: 1\nmetrics: {}", "metrics"),
        ("version: 1\nmetrics: {a: {max_absolute_degradation: -1}}", "greater than or equal to 0"),
        ("version: 1\nmetrics: {a: {max_absolute_degradation: .nan}}", "finite"),
        (
            "version: 1\ndefaults: {confidence: 1.5}\nmetrics: {a: {max_absolute_degradation: 1}}",
            "confidence",
        ),
        (
            "version: 1\ndefaults: {min_samples: 1}\nmetrics: {a: {max_absolute_degradation: 1}}",
            "min_samples",
        ),
        ("version: 1\nmetrics: {a: {max_absolute_degradation: 1, typo: 1}}", "typo"),
        ("- a list", "mapping"),
        ("version: [1", "invalid YAML"),
    ],
)
def test_invalid_configs(tmp_path: Path, text: str, fragment: str) -> None:
    with pytest.raises(SchemaError) as info:
        load_config(write(tmp_path, text))
    assert fragment in str(info.value)


def test_missing_config_file(tmp_path: Path) -> None:
    with pytest.raises(SchemaError, match="cannot read file"):
        load_config(tmp_path / "nope.yaml")
