from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from .held_out_review_v2_contracts import ReviewSamplingStratumV2R2
from .ids import stable_id
from .review_compression_contracts import (
    AtomicConflictWitness,
    ConflictReviewCluster,
    ConflictSiteReviewCase,
    NegativePairSample,
    NegativePairStratum,
)


def deterministic_sample(
    values: Sequence[str],
    count: int,
    *,
    seed: str,
    namespace: str,
) -> tuple[str, ...]:
    """Draw a reproducible, order-independent sample without replacement."""

    unique = tuple(sorted(set(values)))
    if count < 0 or count > len(unique):
        raise ValueError("Deterministic sample size is outside the population.")
    ranked = sorted(
        unique,
        key=lambda value: (
            _seeded_digest(seed, namespace, value),
            value,
        ),
    )
    return tuple(ranked[:count])


def allocate_stratified_sample_sizes(
    population_by_stratum: Mapping[tuple[str, ...], int],
    target: int,
    *,
    seed: str,
    namespace: str,
) -> dict[tuple[str, ...], int]:
    """Allocate at least one draw per stratum, then balance inclusion rates."""

    populations = dict(sorted(population_by_stratum.items()))
    if not populations or any(count < 1 for count in populations.values()):
        raise ValueError("Stratified sampling requires non-empty positive populations.")
    draw_count = min(target, sum(populations.values()))
    if draw_count < len(populations):
        raise ValueError("Sampling target cannot cover every observed stratum.")
    selected = {key: 1 for key in populations}
    while sum(selected.values()) < draw_count:
        eligible = [key for key, size in populations.items() if selected[key] < size]
        if not eligible:
            break
        round_index = sum(selected.values())
        chosen = min(
            eligible,
            key=lambda key: (
                selected[key] / populations[key],
                _seeded_digest(
                    seed,
                    namespace,
                    str(round_index),
                    "|".join(key),
                ),
                key,
            ),
        )
        selected[chosen] += 1
    return selected


def conflict_site_stratum_key(
    site: ConflictSiteReviewCase,
    witness_by_id: Mapping[str, AtomicConflictWitness],
) -> tuple[str, str]:
    witnesses = tuple(witness_by_id[witness_id] for witness_id in site.atomic_witness_ids)
    certainties = {witness.certainty for witness in witnesses}
    if certainties == {"confirmed"}:
        certainty_profile = "confirmed-only"
    elif certainties == {"potential"}:
        certainty_profile = "potential-only"
    else:
        certainty_profile = "mixed"
    cross_cell_or_rail = any(
        witness.grade_relation != "same-physical-cell"
        or any("rail" in road_class or "tram" in road_class for road_class in witness.road_classes)
        for witness in witnesses
    )
    grade_risk_profile = "cross-cell-or-rail" if cross_cell_or_rail else "same-cell"
    return certainty_profile, grade_risk_profile


def select_conflict_sites(
    *,
    corridor_key: str,
    sites: Sequence[ConflictSiteReviewCase],
    witness_by_id: Mapping[str, AtomicConflictWitness],
    target: int,
    seed: str,
) -> tuple[
    tuple[ConflictSiteReviewCase, float],
    tuple[ReviewSamplingStratumV2R2, ...],
]:
    by_stratum: dict[tuple[str, ...], list[ConflictSiteReviewCase]] = {}
    for site in sites:
        key = conflict_site_stratum_key(site, witness_by_id)
        by_stratum.setdefault(key, []).append(site)
    allocations = allocate_stratified_sample_sizes(
        {key: len(group) for key, group in by_stratum.items()},
        target,
        seed=seed,
        namespace=f"conflict-site-allocation|{corridor_key}",
    )
    selected: list[tuple[ConflictSiteReviewCase, float]] = []
    strata: list[ReviewSamplingStratumV2R2] = []
    for key, group in sorted(by_stratum.items()):
        count = allocations[key]
        by_id = {site.site_review_case_id: site for site in group}
        selected_ids = deterministic_sample(
            tuple(by_id),
            count,
            seed=seed,
            namespace=f"conflict-site-members|{corridor_key}|{'|'.join(key)}",
        )
        probability = count / len(group)
        selected.extend((by_id[site_id], probability) for site_id in selected_ids)
        identity = {
            "corridor_key": corridor_key,
            "unit_kind": "conflict-site",
            "stratum_key": key,
            "selection_source": "full-population",
            "population_count": len(group),
            "available_sample_count": len(group),
        }
        strata.append(
            ReviewSamplingStratumV2R2(
                stratum_id=stable_id("scope", identity),
                selected_count=count,
                inclusion_probability=probability,
                **identity,
            )
        )
    return tuple(sorted(selected, key=lambda item: item[0].site_review_case_id)), tuple(
        sorted(strata, key=lambda item: item.stratum_id)
    )


def select_negative_pairs(
    *,
    corridor_key: str,
    strata: Sequence[NegativePairStratum],
    target: int,
    seed: str,
) -> tuple[
    tuple[NegativePairSample, float],
    tuple[ReviewSamplingStratumV2R2, ...],
]:
    if not strata:
        raise ValueError(f"Corridor {corridor_key} has no negative-pair strata.")
    stratum_by_key = {
        (
            stratum.control_class,
            "+".join(stratum.conflicting_turn_classes),
            stratum.traffic_side.value,
        ): stratum
        for stratum in strata
    }
    if len(stratum_by_key) != len(strata):
        raise ValueError("Negative-pair stratum keys are not unique.")
    allocations = allocate_stratified_sample_sizes(
        {key: len(stratum.samples) for key, stratum in stratum_by_key.items()},
        target,
        seed=seed,
        namespace=f"negative-pair-allocation|{corridor_key}",
    )
    selected: list[tuple[NegativePairSample, float]] = []
    ledger_strata: list[ReviewSamplingStratumV2R2] = []
    for key, stratum in sorted(stratum_by_key.items()):
        count = allocations[key]
        by_id = {sample.sample_id: sample for sample in stratum.samples}
        selected_ids = deterministic_sample(
            tuple(by_id),
            count,
            seed=seed,
            namespace=f"negative-pair-members|{corridor_key}|{'|'.join(key)}",
        )
        probability = count / stratum.population_count
        selected.extend((by_id[sample_id], probability) for sample_id in selected_ids)
        identity = {
            "corridor_key": corridor_key,
            "unit_kind": "negative-pair",
            "stratum_key": key,
            "selection_source": "preselected-probability-sample",
            "population_count": stratum.population_count,
            "available_sample_count": len(stratum.samples),
        }
        ledger_strata.append(
            ReviewSamplingStratumV2R2(
                stratum_id=stable_id("scope", identity),
                selected_count=count,
                inclusion_probability=probability,
                **identity,
            )
        )
    return tuple(sorted(selected, key=lambda item: item[0].sample_id)), tuple(
        sorted(ledger_strata, key=lambda item: item.stratum_id)
    )


def select_presented_site_witnesses(
    *,
    site: ConflictSiteReviewCase,
    cluster_by_id: Mapping[str, ConflictReviewCluster],
    seed: str,
    maximum_visible_representatives: int = 5,
) -> tuple[tuple[str, ...], str | None]:
    """Select bounded representatives and a role-hidden independent member."""

    representative_ids = tuple(
        sorted(
            {
                witness_id
                for cluster_id in site.cluster_ids
                for witness_id in cluster_by_id[cluster_id].visible_representative_witness_ids
            }
        )
    )
    visible = list(
        deterministic_sample(
            representative_ids,
            min(maximum_visible_representatives, len(representative_ids)),
            seed=seed,
            namespace=f"site-representatives|{site.site_review_case_id}",
        )
    )
    hidden_candidates = tuple(sorted(set(site.atomic_witness_ids) - set(visible)))
    hidden: str | None = None
    if hidden_candidates:
        hidden = deterministic_sample(
            hidden_candidates,
            1,
            seed=seed,
            namespace=f"site-hidden|{site.site_review_case_id}",
        )[0]
    elif len(site.atomic_witness_ids) > 1 and visible:
        hidden = deterministic_sample(
            tuple(visible),
            1,
            seed=seed,
            namespace=f"site-hidden-from-representatives|{site.site_review_case_id}",
        )[0]
        visible.remove(hidden)
    presented = tuple(sorted((*visible, *((hidden,) if hidden is not None else ()))))
    if not presented:
        raise ValueError("Selected conflict sites require presented witnesses.")
    return presented, hidden


def _seeded_digest(seed: str, *parts: str) -> str:
    payload = "\x1f".join((seed, *parts))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
