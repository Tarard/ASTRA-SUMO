"""Small, source-bound design checks. No rule writes observed road geometry.

Each function implements one reviewed fragment, not whole-standard compliance.
Unknown context stays unresolved. Planning widths are not field observations.
"""

from collections.abc import Mapping
from datetime import date
import math

from .contracts import RoadObjectRef


RESTRA = "restra-2017-revision-2026-03-23"
RAST = "rast-2006-en-2012"
_BOOKS = {
    RESTRA: dict(provider="Hamburg BVM", jurisdiction="DE-HH", edition="2017, revision 2026-03-23",
                 source_sha256="76537cfc6757b04ac1ffd9e943a5d4b0e3b89af2ce3a1bda64dd29a9fae3516a",
                 url="https://dokumente.hamburg.de/resource/blob/193072/34178351da128e832be75bb3c3fcc405/restra-data.pdf"),
    RAST: dict(provider="FGSV", jurisdiction="DE", edition="2006, official English translation 2012",
               source_sha256="86b92af27db4401b95005777ed36dd66a23e38248800b06d76a35005eed3380f",
               url="https://www.fgsv-verlag.de/pub/media/pdf/200_E_PDF.v.pdf"),
}


def _source(book_id, section, printed_page, pdf_page):
    book = _BOOKS[book_id]
    ref = RoadObjectRef(namespace="design_standard", object_type="clause", object_id=section,
                        dataset=book_id, **{k: book[k] for k in ("provider", "jurisdiction", "edition", "source_sha256")})
    return dict(ref=ref.as_dict(), url=book["url"], section=section, printed_page=printed_page, pdf_page=pdf_page)


def _base(rule_id, sources):
    return dict(rule_id=rule_id, status="review_required", applicability="unresolved", reasons=[], sources=sources,
                numeric_evaluation_performed=False, geometry_changes_authorized=False,
                field_conformity="not_assessed", check_scope="cited_rule_fragment_only",
                claim_boundary="A result checks only the cited dimensions and caller-declared conditions. It does not certify built conditions, complete design compliance, or authorize lane, access, or connection changes.")


def _stop(result, status, reason):
    result.update(status=status, applicability="not_applicable" if status == "not_applicable" else "unresolved")
    result["reasons"].append(reason)
    return False


def _context(result, context, book_id):
    if context is None:
        return _stop(result, "review_required", "missing_design_context")
    if not isinstance(context, Mapping):
        raise ValueError("context must be an object.")
    result["context"] = dict(context)
    fields = ("purpose", "jurisdiction", "assessment_date", "standard_id")
    missing = [key for key in fields if context.get(key) in (None, "", "unknown")]
    if missing:
        return _stop(result, "review_required", "missing_context:" + ",".join(missing))
    if not all(isinstance(context[key], str) for key in fields):
        raise ValueError("Context fields must be text.")
    stamp = date.fromisoformat(context["assessment_date"])
    if stamp.isoformat() != context["assessment_date"]:
        raise ValueError("Use a YYYY-MM-DD assessment date.")
    if context["purpose"] == "existing_road_reconstruction":
        return _stop(result, "not_applicable", "design_values_do_not_replace_existing_road_observations")
    if context["purpose"] != "design_review":
        raise ValueError("purpose must be design_review or existing_road_reconstruction.")
    if context["standard_id"] != book_id:
        return _stop(result, "not_applicable", "different_standard_version")
    jurisdiction = context["jurisdiction"]
    if (book_id == RESTRA and jurisdiction != "DE-HH") or (book_id == RAST and not (jurisdiction == "DE" or jurisdiction.startswith("DE-"))):
        return _stop(result, "not_applicable", "outside_source_jurisdiction")
    if (book_id == RESTRA and stamp < date(2026, 3, 23)) or (book_id == RAST and stamp.year < 2006):
        return _stop(result, "not_applicable", "reference_postdates_assessment")
    if book_id == RAST:
        _boolean(context.get("local_overrides_checked"), "local_overrides_checked")
        if context.get("local_overrides_checked") is not True:
            return _stop(result, "review_required", "local_overrides_not_checked")
    result["applicability"] = "caller_context_matches_reviewed_scope"
    return True


def _boolean(value, label):
    if value is not None and type(value) is not bool:
        raise ValueError(f"{label} must be true, false, or null.")


def _number(value, label, *, allow_zero=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number.")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{label} must be finite.") from error
    if not math.isfinite(number) or number < 0 or (not allow_zero and number == 0):
        raise ValueError(f"{label} must be finite and {'nonnegative' if allow_zero else 'positive'}.")
    return number


def _interval(value, uncertainty, label="width_m"):
    width = _number(value, label)
    error = _number(uncertainty, "uncertainty_m", allow_zero=True)
    if width is None or error is None:
        return None
    if width - error <= 0 or not math.isfinite(width + error):
        raise ValueError("The complete width interval must be finite and positive.")
    return [width - error, width + error]


def _conditions(result, conditions):
    conditions = [(key, _condition_text(actual, key) if isinstance(expected, str) else actual, expected)
                  for key, actual, expected in conditions]
    missing = [key for key, actual, _ in conditions if actual is None or actual == "unknown"]
    if missing:
        return _stop(result, "review_required", "missing_conditions:" + ",".join(missing))
    different = [key for key, actual, expected in conditions if actual != expected]
    if different:
        return _stop(result, "not_applicable", "different_conditions:" + ",".join(different))
    return True


def _condition_text(value, label):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text or null.")
    return None if value.strip() in ("", "unknown") else value.strip()


def _basis_and_value(result, interval, actual, expected):
    actual = _condition_text(actual, "width_basis")
    if actual != expected:
        return _stop(result, "review_required", f"width_basis_requires:{expected}")
    if interval is None:
        return _stop(result, "review_required", "width_or_uncertainty_unknown")
    result.update(width_interval_m=interval, numeric_evaluation_performed=True)
    return True


def _minimum(interval, threshold):
    return "met" if interval[0] >= threshold else "not_met" if interval[1] < threshold else "uncertain"


def check_cycle_lane_width(width_m, *, context, facility_type=None, width_basis="unknown", uncertainty_m=0.0):
    """ReStra 2026 section 6.1.7.4: Radfahrstreifen widths include their marking."""
    result = _base("restra.radfahrstreifen.width.2026", [_source(RESTRA, "6.1.7.4 paragraph 3", "24", 37)])
    result["conditions"] = dict(facility_type=facility_type, width_basis=width_basis)
    interval = _interval(width_m, uncertainty_m)
    if not _context(result, context, RESTRA) or not _conditions(result, [("facility_type", facility_type, "radfahrstreifen")]):
        return result
    if not _basis_and_value(result, interval, width_basis, "including_markings"):
        return result
    result.update(minimum_width_m=2.25, regular_width_m=2.75, minimum_check=_minimum(interval, 2.25),
                  regular_check=_minimum(interval, 2.75), source_wording="Regelbreite / Mindestbreite")
    if result["regular_check"] == "met":
        result.update(status="pass", reasons=["listed_width_thresholds_met"])
    elif result["minimum_check"] == "met":
        result["reasons"] = ["uncertainty_crosses_regular_width" if result["regular_check"] == "uncertain"
                             else "below_regular_width_requires_design_review"]
    else:
        result["reasons"] = ["below_minimum_width" if result["minimum_check"] == "not_met" else "uncertainty_crosses_minimum"]
    return result


def check_two_lane_carriageway_width(width_m, *, context, road_type=None, lane_count=None, directionality=None,
                                    infrequent_hgv_meetings=None, low_speed_meetings=None,
                                    scheduled_bus_service=None, advisory_cycle_lanes_present=None,
                                    width_basis="unknown", uncertainty_m=0.0):
    """ReStra's replacement for one RASt table row, not a universal road minimum."""
    result = _base("restra.two_lane_hgv_meeting.width.2026", [
        _source(RESTRA, "6.1.1.2 Table 7 row 4", "16", 29),
        _source(RAST, "6.1.1.2 Table 7 row 4 (overridden width)", "68", 70)])
    interval = _interval(width_m, uncertainty_m)
    for label, value in (("infrequent_hgv_meetings", infrequent_hgv_meetings), ("low_speed_meetings", low_speed_meetings),
                         ("scheduled_bus_service", scheduled_bus_service), ("advisory_cycle_lanes_present", advisory_cycle_lanes_present)):
        _boolean(value, label)
    if lane_count is not None and (type(lane_count) is not int or lane_count < 1):
        raise ValueError("lane_count must be a positive integer or null.")
    conditions = [("road_type", road_type, "urban_main_road"), ("lane_count", lane_count, 2),
                  ("directionality", directionality, "two_way"), ("infrequent_hgv_meetings", infrequent_hgv_meetings, True),
                  ("low_speed_meetings", low_speed_meetings, True)]
    result["conditions"] = {key: actual for key, actual, _ in conditions}
    result["implementation_scope_limits"] = dict(scheduled_bus_service=scheduled_bus_service,
        advisory_cycle_lanes_present=advisory_cycle_lanes_present,
        reason="This first check excludes cases with other Table 7 rows. This is an implementation scope limit, not a source prohibition.")
    if not _context(result, context, RESTRA) or not _conditions(result, conditions):
        return result
    if scheduled_bus_service is None or advisory_cycle_lanes_present is None:
        _stop(result, "review_required", "other_table_row_conditions_unknown")
        return result
    if scheduled_bus_service or advisory_cycle_lanes_present:
        _stop(result, "not_applicable", "outside_reviewed_single_table_row_scope")
        return result
    if not _basis_and_value(result, interval, width_basis, "whole_carriageway"):
        return result
    result.update(reference_width_m=5.90, reference_kind="specified_design_value", source_wording="festgelegt")
    if interval == [5.90, 5.90]:
        result.update(status="pass", reasons=["specified_design_value_matches"])
    else:
        result["reasons"] = ["reference_inside_uncertainty_interval" if interval[0] <= 5.90 <= interval[1]
                             else "different_width_requires_other_design_basis"]
    return result


def check_left_turn_lane_width(width_m, *, context, control_type=None, restricted_space=None,
                               scheduled_bus_service=None, through_lane_width_m=None, width_basis="unknown",
                               uncertainty_m=0.0, through_lane_uncertainty_m=0.0):
    """RASt table 46 applies here only to priority-sign-controlled junctions."""
    result = _base("rast.priority_sign_left_turn.width.2006", [
        _source(RAST, "6.3.3 Table 46 and both footnotes", "106", 108),
        _source(RAST, "6.3.1 turning-lane width and scheduled bus service", "104", 106),
        _source(RAST, "6.3.3 priority-control-sign junction scope", "105", 107)])
    interval = _interval(width_m, uncertainty_m)
    through = _interval(through_lane_width_m, through_lane_uncertainty_m, "through_lane_width_m")
    _boolean(restricted_space, "restricted_space")
    _boolean(scheduled_bus_service, "scheduled_bus_service")
    result["conditions"] = dict(control_type=control_type, restricted_space=restricted_space,
                                 scheduled_bus_service=scheduled_bus_service, through_lane_width_interval_m=through)
    if not _context(result, context, RAST) or not _conditions(result, [("control_type", control_type, "priority_signs")]):
        return result
    if restricted_space is None or scheduled_bus_service is None or through is None:
        _stop(result, "review_required", "space_bus_or_through_lane_condition_unknown")
        return result
    if not _basis_and_value(result, interval, width_basis, "standard_lane_width"):
        return result
    minimum = 3.0 if scheduled_bus_service or not restricted_space else 2.75
    required = [max(minimum, value - 0.25) for value in through]
    comparison = "met" if interval[0] >= required[1] else "not_met" if interval[1] < required[0] else "uncertain"
    result.update(required_width_interval_m=required, maximum_narrowing_from_through_lane_m=0.25,
                  minimum_check=comparison, source_wording="table inequalities and conditional footnotes")
    result.update(status="pass" if comparison == "met" else "review_required",
                  reasons=["listed_dimensions_met" if comparison == "met" else "width_or_uncertainty_requires_review"])
    return result
