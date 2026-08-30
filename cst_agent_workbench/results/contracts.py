"""Stable result envelopes shared by CST readers, services, and Agent tools."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, TypedDict


class ResultKind(str, Enum):
    """Supported result shapes exposed by the canonical CST Agent runtime."""

    SCALAR = "scalar"
    CURVE_1D = "curve_1d"
    S_PARAMETER = "s_parameter"
    FARFIELD_CUT = "farfield_cut"
    ARTIFACT_REF = "artifact_ref"
    UNSUPPORTED_3D = "unsupported_3d"


class CurvePoint(TypedDict, total=False):
    x: float
    y: float
    freq: float
    s_db: float
    angle_deg: float
    gain_dbi: float


class ResultEnvelope(TypedDict, total=False):
    success: bool
    message: str
    item: str
    type: str
    result_kind: str
    points: int
    total_points: int
    returned_points: int
    downsampled: bool
    plot_data: list[CurvePoint]


@dataclass(frozen=True)
class DownsampledCurve:
    points: list[dict[str, Any]]
    total_points: int

    @property
    def returned_points(self) -> int:
        return len(self.points)

    @property
    def downsampled(self) -> bool:
        return self.returned_points < self.total_points

    def metadata(self) -> dict[str, Any]:
        return {
            "points": self.total_points,
            "total_points": self.total_points,
            "returned_points": self.returned_points,
            "downsampled": self.downsampled,
        }


def downsample_curve(
    points: list[dict[str, Any]],
    *,
    max_points: int | None,
    y_key: str,
) -> DownsampledCurve:
    """Select a deterministic bounded view while retaining endpoints and extrema.

    The original curve is used for all scalar summaries.  Only ``plot_data`` is
    reduced.  Keeping the global minimum and maximum protects S-parameter
    notches and farfield peaks from disappearing in the model-facing payload.
    """

    total = len(points)
    if max_points is None or total <= max_points:
        return DownsampledCurve(list(points), total)
    if max_points < 4:
        raise ValueError("max_points must be at least 4")

    numeric_y: list[tuple[int, float]] = []
    for index, point in enumerate(points):
        try:
            numeric_y.append((index, float(point[y_key])))
        except (KeyError, TypeError, ValueError):
            continue

    selected = {0, total - 1}
    if numeric_y:
        selected.add(min(numeric_y, key=lambda item: item[1])[0])
        selected.add(max(numeric_y, key=lambda item: item[1])[0])

    # Fill the remaining slots from a uniform grid.  A denser second grid only
    # resolves collisions with protected extrema; it does not bias toward the
    # beginning of the curve.
    remaining = max_points - len(selected)
    for grid_index in range(1, remaining + 1):
        selected.add(round(grid_index * (total - 1) / (remaining + 1)))
    if len(selected) < max_points:
        dense_grid_size = max_points * 4
        for grid_index in range(1, dense_grid_size):
            if len(selected) >= max_points:
                break
            selected.add(round(grid_index * (total - 1) / dense_grid_size))

    if len(selected) < max_points:
        for candidate in range(total):
            selected.add(candidate)
            if len(selected) >= max_points:
                break

    indices = sorted(selected)[:max_points]
    return DownsampledCurve([points[index] for index in indices], total)


def paginate_items(
    items: list[str],
    *,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """Return a stable offset page without leaking the full result tree."""

    total = len(items)
    page = items[offset : offset + limit]
    next_offset = offset + len(page)
    has_more = next_offset < total
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "returned": len(page),
        "has_more": has_more,
        "next_offset": next_offset if has_more else None,
        "items": page,
    }


def validate_result_envelope(payload: dict[str, Any]) -> None:
    """Fail fast when a successful canonical result violates its discriminator."""

    if not payload.get("success"):
        return
    raw_kind = payload.get("result_kind")
    try:
        ResultKind(str(raw_kind))
    except ValueError as exc:
        raise ValueError(f"unsupported result_kind: {raw_kind!r}") from exc

    plot_data = payload.get("plot_data")
    if plot_data is None:
        return
    if not isinstance(plot_data, list):
        raise ValueError("plot_data must be a list")
    returned = payload.get("returned_points")
    total = payload.get("total_points")
    points = payload.get("points")
    if type(returned) is not int or returned < 0:
        raise ValueError("returned_points must be a non-negative integer")
    if type(total) is not int or total < 0:
        raise ValueError("total_points must be a non-negative integer")
    if points is not None and (type(points) is not int or points < 0):
        raise ValueError("points must be a non-negative integer")
    if returned != len(plot_data):
        raise ValueError("returned_points must equal len(plot_data)")
    if total < returned:
        raise ValueError("total_points must be an integer >= returned_points")
    if points is not None and points != total:
        raise ValueError("points must equal total_points")
    if bool(payload.get("downsampled")) != (returned < total):
        raise ValueError("downsampled must equal returned_points < total_points")
