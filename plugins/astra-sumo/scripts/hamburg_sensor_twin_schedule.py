from __future__ import annotations

import argparse
import csv
import statistics
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

A = "141050975"
B = "186821027#0"
C = "186821036#1"
D = "24483192"
SENSORS = (A, B, C, D)
C_PASS_EDGES = ("186821034#0", "186821035#0")
C_034_STREAM_IDS = {31892, 33055}
C_035_STREAM_IDS = {29038}
A_244_STREAM_IDS = {29437, 30494}
A_603_STREAM_IDS = {29375}
B_CORRIDOR_STREAM_IDS = {30894, 33855}
B_LOCAL_STREAM_IDS = {29316, 34287}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prior-demand", required=True, type=Path)
    parser.add_argument("--prior-vehroute", required=True, type=Path)
    parser.add_argument("--edge-counts", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target-shift", type=int, default=1800)
    parser.add_argument("--cd-pair-count", type=int)
    parser.add_argument("--historical-counts-csv", type=Path)
    parser.add_argument("--cd-upstream-stream-id", action="append", type=int, default=[])
    parser.add_argument("--candidate-routes", type=Path)
    parser.add_argument("--c-only-route-id")
    parser.add_argument("--c-only-delay-seconds", type=float)
    parser.add_argument("--historical-local-c", action="store_true")
    parser.add_argument("--c034-delay-seconds", type=float, default=2.0)
    parser.add_argument("--local-d-boundary", action="store_true")
    parser.add_argument("--local-d-delay-seconds", type=float)
    parser.add_argument("--preserve-ab", action="store_true")
    parser.add_argument("--historical-local-ab", action="store_true")
    parser.add_argument("--b-local-delay-seconds", type=float, default=2.2)
    parser.add_argument("--a244-delay-seconds", type=float, default=13.8)
    parser.add_argument("--a603-delay-seconds", type=float, default=0.33)
    parser.add_argument("--damping", type=float, default=1.0)
    parser.add_argument("--depart-lane", choices=("best", "free"), default="best")
    args = parser.parse_args()

    targets = _targets(args.edge_counts)
    historical_profile = None
    if bool(args.historical_counts_csv) != bool(args.cd_upstream_stream_id):
        raise ValueError(
            "historical counts and cd upstream stream ids must be supplied together"
        )
    if args.historical_counts_csv:
        historical_profile = _historical_profile(
            args.historical_counts_csv,
            set(args.cd_upstream_stream_id),
        )
    c034_profile = None
    c035_profile = None
    if args.historical_local_c:
        if args.historical_counts_csv is None or args.c034_delay_seconds <= 0:
            raise ValueError(
                "historical local C requires historical counts and positive delay"
            )
        c034_profile = _historical_profile(
            args.historical_counts_csv,
            C_034_STREAM_IDS,
        )
        c035_profile = _historical_profile(
            args.historical_counts_csv,
            C_035_STREAM_IDS,
        )
        if np.any(np.asarray(c034_profile) + np.asarray(c035_profile) != targets[C]):
            raise ValueError("historical local C profiles do not reproduce C targets")
    a244_profile = None
    a603_profile = None
    b_corridor_profile = None
    b_local_profile = None
    if args.historical_local_ab:
        if (
            args.historical_counts_csv is None
            or args.preserve_ab
            or min(
                args.b_local_delay_seconds,
                args.a244_delay_seconds,
                args.a603_delay_seconds,
            )
            <= 0
        ):
            raise ValueError(
                "historical local AB requires historical counts, positive delays, and no preserve flag"
            )
        a244_profile = _historical_profile(
            args.historical_counts_csv,
            A_244_STREAM_IDS,
        )
        a603_profile = _historical_profile(
            args.historical_counts_csv,
            A_603_STREAM_IDS,
        )
        b_corridor_profile = _historical_profile(
            args.historical_counts_csv,
            B_CORRIDOR_STREAM_IDS,
        )
        b_local_profile = _historical_profile(
            args.historical_counts_csv,
            B_LOCAL_STREAM_IDS,
        )
        if np.any(np.asarray(a244_profile) + np.asarray(a603_profile) != targets[A]):
            raise ValueError("historical local A profiles do not reproduce A targets")
        if np.any(
            np.asarray(b_corridor_profile) + np.asarray(b_local_profile) != targets[B]
        ):
            raise ValueError("historical local B profiles do not reproduce B targets")
    templates, totals = _templates(args.prior_demand)
    delays = _delays(args.prior_demand, args.prior_vehroute)
    by_signature = {_signature(route): route for route in templates}
    required = {(A, B), (A,), (C, D), (C,), (D,)}
    if set(by_signature) != required:
        raise ValueError(f"unexpected route signatures: {sorted(by_signature)}")

    vehicles: list[tuple[float, tuple[str, ...]]] = []
    prior_route_ba = by_signature[(A, B)]
    route_ba = prior_route_ba
    route_a = by_signature[(A,)]
    prior_route_cd = by_signature[(C, D)]
    route_cd = prior_route_cd
    route_c = by_signature[(C,)]
    route_d = by_signature[(D,)]
    route_depart_lanes: dict[tuple[str, ...], str] = {}
    route_b_local = (B, C)
    route_a244 = ("24483344#0", A)
    route_a603 = ("603103445#0", A)
    if args.historical_local_ab:
        route_ba = route_ba[route_ba.index(B) :]
        delays[route_b_local] = {B: float(args.b_local_delay_seconds)}
        delays[route_a244] = {A: float(args.a244_delay_seconds)}
        delays[route_a603] = {A: float(args.a603_delay_seconds)}
        route_depart_lanes[route_ba] = "0"
        route_depart_lanes[route_b_local] = "2"
        route_depart_lanes[route_a244] = "free"
        route_depart_lanes[route_a603] = "0"
    override_values = (
        args.candidate_routes,
        args.c_only_route_id,
        args.c_only_delay_seconds,
    )
    if any(value is not None for value in override_values):
        if not all(value is not None for value in override_values):
            raise ValueError(
                "candidate routes, c-only route id, and c-only delay must be supplied together"
            )
        route_c = _candidate_route(args.candidate_routes, args.c_only_route_id)
        if _signature(route_c) != (C,) or args.c_only_delay_seconds <= 0:
            raise ValueError("c-only route override must pass only C with positive delay")
        delays[route_c] = {C: float(args.c_only_delay_seconds)}
        route_depart_lanes[route_c] = "3"
    route_c034 = ("186821034#0", C)
    if args.historical_local_c:
        delays[route_c034] = {C: float(args.c034_delay_seconds)}
        route_depart_lanes[route_c034] = "free"
    if args.local_d_boundary:
        if args.local_d_delay_seconds is None or args.local_d_delay_seconds <= 0:
            raise ValueError("local D boundary requires a positive detector delay")
        route_cd = route_cd[route_cd.index(D) :]
        d_start = route_d.index(D)
        route_d = route_d[d_start : d_start + 2]
        delays[route_d] = {D: float(args.local_d_delay_seconds)}
        route_depart_lanes[route_d] = "0"
        route_depart_lanes[route_cd] = "1"
    elif args.local_d_delay_seconds is not None:
        raise ValueError("local D delay requires --local-d-boundary")

    if args.historical_local_ab:
        ba_classes = _scale_classes(
            _pair_delay_classes(
                args.prior_demand,
                args.prior_vehroute,
                prior_route_ba,
                B,
                A,
            ),
            sum(b_corridor_profile),
        )
        local_ba_classes: Counter[tuple[int, int]] = Counter()
        for (_upstream_delay, transit_delay), count in ba_classes.items():
            local_ba_classes[
                (round(float(args.b_local_delay_seconds)), transit_delay)
            ] += count
        ba_allocation = _paired_class_allocation(
            dict(local_ba_classes),
            b_corridor_profile,
            a603_profile,
        )
        paired_b, paired_a = _append_paired_vehicles(
            vehicles,
            route_ba,
            ba_allocation,
            args.target_shift,
        )
        if paired_b.tolist() != b_corridor_profile:
            raise ValueError("historical B corridor profile was not fully allocated")
        a603_only = np.asarray(a603_profile) - paired_a
        if np.any(a603_only < 0):
            raise ValueError("paired A-B flow exceeds historical A603 flow")
        for bin_index, count in enumerate(b_local_profile):
            for pass_b in _even_times(args.target_shift + bin_index * 900, count):
                vehicles.append((pass_b - delays[route_b_local][B], route_b_local))
        for bin_index, count in enumerate(a244_profile):
            for pass_a in _even_times(args.target_shift + bin_index * 900, count):
                vehicles.append((pass_a - delays[route_a244][A], route_a244))
        for bin_index, count in enumerate(a603_only.tolist()):
            for pass_a in _even_times(args.target_shift + bin_index * 900, count):
                vehicles.append((pass_a - delays[route_a603][A], route_a603))
    elif args.preserve_ab:
        _append_prior_signatures(
            vehicles,
            args.prior_demand,
            {(A,), (A, B)},
        )
    else:
        ba_classes = _pair_delay_classes(
            args.prior_demand,
            args.prior_vehroute,
            route_ba,
            B,
            A,
        )
        ba_allocation = _paired_class_allocation(ba_classes, targets[B], targets[A])
        paired_b, paired_a = _append_paired_vehicles(
            vehicles, route_ba, ba_allocation, args.target_shift
        )
        if paired_b.tolist() != targets[B]:
            raise ValueError("B-to-A allocation did not consume every upstream target")
        a_only = [
            target - paired
            for target, paired in zip(targets[A], paired_a, strict=True)
        ]
        if min(a_only) < 0 or sum(a_only) != totals[route_a]:
            raise ValueError(f"B-to-A schedule cannot preserve A-only demand: {a_only}")
        for bin_index, count in enumerate(a_only):
            for pass_a in _even_times(args.target_shift + bin_index * 900, count):
                vehicles.append((pass_a - delays[route_a][A], route_a))

    pair_count = (
        totals[prior_route_cd]
        if args.cd_pair_count is None
        else args.cd_pair_count
    )
    if historical_profile is not None:
        historical_total = sum(historical_profile)
        if args.cd_pair_count is not None and pair_count != historical_total:
            raise ValueError("cd-pair-count conflicts with historical branch counts")
        pair_count = historical_total
    if not 0 <= pair_count <= min(sum(targets[C]), sum(targets[D])):
        raise ValueError("cd-pair-count is outside the observed station totals")
    cd_classes = _scale_classes(
        _pair_delay_classes(
            args.prior_demand,
            args.prior_vehroute,
            prior_route_cd,
            D,
            C,
        ),
        pair_count,
    )
    if args.local_d_boundary:
        local_classes: Counter[tuple[int, int]] = Counter()
        for (_upstream_delay, transit_delay), count in cd_classes.items():
            local_classes[
                (round(float(args.local_d_delay_seconds)), transit_delay)
            ] += count
        cd_classes = dict(local_classes)
    cd_allocation = (
        _paired_class_allocation(
            cd_classes,
            historical_profile if historical_profile is not None else targets[D],
            targets[C],
        )
        if cd_classes
        else []
    )
    paired_d, paired_c = _append_paired_vehicles(
        vehicles, route_cd, cd_allocation, args.target_shift
    )
    d_only = np.asarray(targets[D]) - paired_d
    c_only = np.asarray(targets[C]) - paired_c
    if int(d_only.sum()) != sum(targets[D]) - pair_count or int(
        c_only.sum()
    ) != sum(targets[C]) - pair_count:
        raise ValueError("C-D allocation does not preserve station totals")
    for bin_index, count in enumerate(d_only.tolist()):
        for pass_d in _even_times(args.target_shift + bin_index * 900, count):
            vehicles.append((pass_d - delays[route_d][D], route_d))
    if args.historical_local_c:
        c034_only = np.asarray(c034_profile) - paired_c
        if np.any(c034_only < 0):
            raise ValueError("paired C-D flow exceeds historical C034 flow")
        for bin_index, count in enumerate(c034_only.tolist()):
            for pass_c in _even_times(args.target_shift + bin_index * 900, count):
                vehicles.append((pass_c - delays[route_c034][C], route_c034))
        for bin_index, count in enumerate(c035_profile):
            for pass_c in _even_times(args.target_shift + bin_index * 900, count):
                vehicles.append((pass_c - delays[route_c][C], route_c))
    else:
        for bin_index, count in enumerate(c_only.tolist()):
            for pass_c in _even_times(args.target_shift + bin_index * 900, count):
                vehicles.append((pass_c - delays[route_c][C], route_c))

    if not 0 < args.damping <= 1:
        raise ValueError("damping must be in (0, 1]")
    if args.damping < 1:
        vehicles = _damp_departures(args.prior_demand, vehicles, args.damping)
    if min(depart for depart, _route in vehicles) < 0:
        raise ValueError("target shift is too short for the observed route delays")
    _write(
        args.output,
        vehicles,
        depart_lane=args.depart_lane,
        route_depart_lanes=route_depart_lanes,
    )
    return 0


def _targets(path: Path) -> dict[str, list[int]]:
    result = {sensor: [] for sensor in SENSORS}
    for interval in ET.parse(path.resolve(strict=True)).getroot().findall("interval"):
        values = {edge.attrib["id"]: int(edge.attrib["count"]) for edge in interval.findall("edge")}
        for sensor in SENSORS:
            result[sensor].append(values[sensor])
    return result


def _templates(path: Path) -> tuple[set[tuple[str, ...]], dict[tuple[str, ...], int]]:
    totals: dict[tuple[str, ...], int] = defaultdict(int)
    for vehicle in ET.parse(path.resolve(strict=True)).getroot().findall("vehicle"):
        route = vehicle.find("route")
        if route is not None:
            totals[tuple(route.attrib["edges"].split())] += 1
    return set(totals), dict(totals)


def _delays(demand_path: Path, vehroute_path: Path) -> dict[tuple[str, ...], dict[str, float]]:
    scheduled = _scheduled_departures(demand_path)
    values: dict[tuple[str, ...], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for vehicle in ET.parse(vehroute_path.resolve(strict=True)).getroot().findall("vehicle"):
        route = vehicle.find("route")
        if route is None:
            continue
        edges = tuple(route.attrib["edges"].split())
        exit_times = [float(value) for value in route.attrib["exitTimes"].split()]
        depart = scheduled[vehicle.attrib["id"]]
        for sensor in SENSORS:
            pass_edge = _pass_edge(edges, sensor)
            if pass_edge is not None:
                values[edges][sensor].append(
                    exit_times[edges.index(pass_edge)] - depart
                )
    return {
        route: {sensor: statistics.median(samples) for sensor, samples in by_sensor.items()}
        for route, by_sensor in values.items()
    }


def _pair_delay_classes(
    demand_path: Path,
    vehroute_path: Path,
    route_filter: tuple[str, ...],
    upstream: str,
    downstream: str,
) -> dict[tuple[int, int], int]:
    scheduled = _scheduled_departures(demand_path)
    classes: Counter[tuple[int, int]] = Counter()
    for vehicle in ET.parse(vehroute_path.resolve(strict=True)).getroot().findall("vehicle"):
        route = vehicle.find("route")
        if route is None or tuple(route.attrib["edges"].split()) != route_filter:
            continue
        edges = tuple(route.attrib["edges"].split())
        exit_times = [float(value) for value in route.attrib["exitTimes"].split()]
        depart = scheduled[vehicle.attrib["id"]]
        upstream_edge = _pass_edge(edges, upstream)
        downstream_edge = _pass_edge(edges, downstream)
        if upstream_edge is None or downstream_edge is None:
            continue
        upstream_delay = exit_times[edges.index(upstream_edge)] - depart
        transit_delay = (
            exit_times[edges.index(downstream_edge)]
            - exit_times[edges.index(upstream_edge)]
        )
        classes[(max(30, round(upstream_delay / 30) * 30), max(30, round(transit_delay / 30) * 30))] += 1
    return dict(classes)


def _scheduled_departures(path: Path) -> dict[str, float]:
    return {
        vehicle.attrib["id"]: float(vehicle.attrib["depart"])
        for vehicle in ET.parse(path.resolve(strict=True)).getroot().findall("vehicle")
    }


def _append_prior_signatures(
    vehicles: list[tuple[float, tuple[str, ...]]],
    demand_path: Path,
    signatures: set[tuple[str, ...]],
) -> None:
    for vehicle in ET.parse(demand_path.resolve(strict=True)).getroot().findall("vehicle"):
        route = vehicle.find("route")
        if route is None:
            continue
        edges = tuple(route.attrib["edges"].split())
        if _signature(edges) in signatures:
            vehicles.append((float(vehicle.attrib["depart"]), edges))


def _signature(route: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sensor for sensor in SENSORS if _pass_edge(route, sensor) is not None)


def _pass_edge(route: tuple[str, ...], sensor: str) -> str | None:
    if sensor == A:
        if A not in route:
            return None
        index = route.index(A)
        return route[index - 1] if index else A
    if sensor == C:
        return next((edge for edge in C_PASS_EDGES if edge in route), None)
    return sensor if sensor in route else None


def _historical_profile(path: Path, stream_ids: set[int]) -> list[int]:
    values: dict[int, int] = defaultdict(int)
    with path.resolve(strict=True).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if int(row["stream_id"]) not in stream_ids:
                continue
            if row.get("quality_status") != "complete":
                raise ValueError("historical turn profile contains an incomplete count bin")
            values[int(row["begin"])] += int(row["expected_total"])
    expected = list(range(0, 9000, 900))
    if sorted(values) != expected:
        raise ValueError("historical turn profile must contain ten 15-minute bins")
    return [values[begin] for begin in expected]


def _candidate_route(path: Path, route_id: str) -> tuple[str, ...]:
    with path.resolve(strict=True).open(encoding="utf-8", newline="") as handle:
        matches = [
            row
            for row in csv.DictReader(handle)
            if row.get("route_id") == route_id
        ]
    if len(matches) != 1:
        raise ValueError(f"candidate route {route_id!r} is not unique")
    route = tuple(matches[0]["edges"].split())
    if not route:
        raise ValueError("candidate route is empty")
    return route


def _scale_classes(classes: dict[tuple[int, int], int], total: int) -> dict[tuple[int, int], int]:
    current = sum(classes.values())
    if total == current:
        return classes
    if total == 0:
        return {}
    scaled = {key: value * total / current for key, value in classes.items()}
    result = {key: int(value) for key, value in scaled.items()}
    remaining = total - sum(result.values())
    for key in sorted(scaled, key=lambda item: scaled[item] - result[item], reverse=True)[:remaining]:
        result[key] += 1
    return {key: value for key, value in result.items() if value}


def _paired_class_allocation(
    classes: dict[tuple[int, int], int], upstream: list[int], downstream: list[int]
) -> list[tuple[tuple[int, int], int, int, int]]:
    class_rows = sorted(classes)
    allowed = []
    costs = []
    for class_index, (_upstream_delay, transit_delay) in enumerate(class_rows):
        for upstream_bin in range(10):
            for downstream_bin in range(10):
                low = max(upstream_bin * 900, downstream_bin * 900 - transit_delay)
                high = min((upstream_bin + 1) * 900, (downstream_bin + 1) * 900 - transit_delay)
                if high > low:
                    allowed.append((class_index, upstream_bin, downstream_bin))
                    costs.append(abs((downstream_bin - upstream_bin) * 900 - transit_delay))
    matrix = np.zeros((len(class_rows) + 20, len(allowed)))
    for column, (class_index, upstream_bin, downstream_bin) in enumerate(allowed):
        matrix[class_index, column] = 1
        matrix[len(class_rows) + upstream_bin, column] = 1
        matrix[len(class_rows) + 10 + downstream_bin, column] = 1
    result = milp(
        c=np.asarray(costs),
        integrality=np.ones(len(allowed)),
        bounds=Bounds(np.zeros(len(allowed)), np.full(len(allowed), np.inf)),
        constraints=LinearConstraint(
            matrix,
            np.concatenate([list(classes[row] for row in class_rows), np.full(20, -np.inf)]),
            np.concatenate([list(classes[row] for row in class_rows), upstream, downstream]),
        ),
    )
    if not result.success or result.x is None:
        raise ValueError("no feasible paired delay-class allocation")
    return [
        (class_rows[class_index], upstream_bin, downstream_bin, int(round(value)))
        for value, (class_index, upstream_bin, downstream_bin) in zip(result.x, allowed, strict=True)
        if round(value) > 0
    ]


def _append_paired_vehicles(
    vehicles: list[tuple[float, tuple[str, ...]]],
    route: tuple[str, ...],
    allocation: list[tuple[tuple[int, int], int, int, int]],
    target_shift: int,
) -> tuple[np.ndarray, np.ndarray]:
    upstream_counts = np.zeros(10, dtype=int)
    downstream_counts = np.zeros(10, dtype=int)
    for (upstream_delay, transit_delay), upstream_bin, downstream_bin, count in allocation:
        low = max(
            target_shift + upstream_bin * 900,
            target_shift + downstream_bin * 900 - transit_delay,
        )
        high = min(
            target_shift + (upstream_bin + 1) * 900,
            target_shift + (downstream_bin + 1) * 900 - transit_delay,
        )
        for upstream_pass in _even_times(low, count, high=high):
            vehicles.append((upstream_pass - upstream_delay, route))
        upstream_counts[upstream_bin] += count
        downstream_counts[downstream_bin] += count
    return upstream_counts, downstream_counts


def _even_times(begin: float, count: int, *, high: float | None = None) -> list[float]:
    end = begin + 900 if high is None else high
    return [begin + (index + 0.5) * (end - begin) / count for index in range(count)]


def _write(
    path: Path,
    vehicles: list[tuple[float, tuple[str, ...]]],
    *,
    depart_lane: str,
    route_depart_lanes: dict[tuple[str, ...], str] | None = None,
) -> None:
    root = ET.Element("routes")
    for index, (depart, route) in enumerate(sorted(vehicles)):
        vehicle = ET.SubElement(
            root,
            "vehicle",
            id=f"sensor_schedule_{index}",
            depart=f"{depart:.2f}",
            departLane=(route_depart_lanes or {}).get(route, depart_lane),
        )
        ET.SubElement(vehicle, "route", edges=" ".join(route))
    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _damp_departures(
    prior_path: Path,
    vehicles: list[tuple[float, tuple[str, ...]]],
    damping: float,
) -> list[tuple[float, tuple[str, ...]]]:
    prior: dict[tuple[str, ...], list[float]] = defaultdict(list)
    for vehicle in ET.parse(prior_path.resolve(strict=True)).getroot().findall("vehicle"):
        route = vehicle.find("route")
        if route is not None:
            prior[tuple(route.attrib["edges"].split())].append(float(vehicle.attrib["depart"]))
    proposed: dict[tuple[str, ...], list[float]] = defaultdict(list)
    for depart, route in vehicles:
        proposed[route].append(depart)
    result = []
    for route, proposed_departures in proposed.items():
        old = sorted(prior[route])
        new = sorted(proposed_departures)
        if len(old) != len(new):
            raise ValueError("damping requires unchanged route totals")
        result.extend((before + damping * (after - before), route) for before, after in zip(old, new, strict=True))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
