"""Number formatting shared by the reporters."""

from __future__ import annotations

from physim_gate.comparison import MetricComparison


def num(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "-"
    if value == 0:
        return "0"
    magnitude = abs(value)
    if magnitude >= 1e5 or magnitude < 1e-3:
        return f"{value:.{digits - 1}e}"
    return f"{value:.{digits}g}"


def signed(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "-"
    text = num(value, digits)
    return text if value <= 0 or text.startswith("+") else f"+{text}"


def interval(bounds: tuple[float, float] | None) -> str:
    if bounds is None:
        return "-"
    return f"[{signed(bounds[0])}, {signed(bounds[1])}]"


def unit_suffix(metric: MetricComparison) -> str:
    return f" {metric.unit}" if metric.unit else ""


def direction_label(metric: MetricComparison) -> str:
    if metric.direction is None:
        return "-"
    return "higher is better" if metric.direction.value == "higher_is_better" else "lower is better"


def percent(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:+.1f}%"
