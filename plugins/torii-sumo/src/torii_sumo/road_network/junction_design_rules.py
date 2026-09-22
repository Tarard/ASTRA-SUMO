"""Check one junction-design recommendation and explicit plan-marking interpretation."""

from collections.abc import Mapping
from pathlib import Path
import re

from ..core.candidate_contracts import file_sha256
from .adapters.engineering_plan import _date, _text, _DOCUMENT_KINDS
from .design_rules import RAST, RESTRA, _base, _boolean, _condition_text, _context, _source, _stop


def check_cycle_lane_position(*, context, position=None, alternative_has_substantial_disadvantages=None,
                              comparison_documented=None, bvm_leadership_decision=None):
    """ReStra ERA 3.3 is a present planning rule, not a historic-road eraser."""
    result = _base('restra.junction.cycle_lane_position.2026', [
        _source(RESTRA, 'ERA 3.3, Radfahrstreifen in Mittellage', '98', 111)])
    for name, value in (('alternative_has_substantial_disadvantages', alternative_has_substantial_disadvantages),
                        ('comparison_documented', comparison_documented)):
        _boolean(value, name)
    position = _condition_text(position, 'position')
    decision = _condition_text(bvm_leadership_decision, 'bvm_leadership_decision')
    if decision not in (None, 'approved', 'rejected', 'pending'):
        raise ValueError('Use approved, rejected, pending, or null for the recorded BVM decision.')
    result.update(conditions=dict(position=position, alternative_has_substantial_disadvantages=alternative_has_substantial_disadvantages,
                                  comparison_documented=comparison_documented, bvm_leadership_decision=decision),
                  connection_changes_authorized=False, historic_layout_changes_authorized=False)
    if not _context(result, context, RESTRA):
        return result
    if position == 'right_of_motor_traffic':
        result.update(status='pass', reasons=['right_side_concentration_matches_cited_position_rule'])
    elif position == 'between_motor_lanes':
        exception = alternative_has_substantial_disadvantages is True and comparison_documented is True and decision == 'approved'
        result.update(status='pass' if exception else 'review_required',
                      reasons=['caller_records_all_exception_conditions' if exception else 'middle_cycle_lane_requires_stated_exception_and_recorded_BVM_decision'])
    else:
        _stop(result, 'review_required', 'cycle_lane_position_unresolved_or_outside_fragment')
    return result


def check_through_lane_continuity(
    *, context, upstream_through_lane_count=None, junction_through_lane_count=None,
    through_lane_becomes_turn_only=None, control_type=None,
    early_unambiguous_markings=None, relevant_signs=None,
):
    """RASt 6.3.1 recommends continuity and states qualified exceptions."""
    result = _base("rast.junction.through_lane_continuity.2006", [
        _source(RAST, "6.3.1 through-lane continuity and exception paragraphs", "104", 106)])
    counts = dict(upstream_through_lane_count=upstream_through_lane_count,
                  junction_through_lane_count=junction_through_lane_count)
    for label, value in counts.items():
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"{label} must be a nonnegative integer or null.")
    flags = dict(through_lane_becomes_turn_only=through_lane_becomes_turn_only,
                 early_unambiguous_markings=early_unambiguous_markings, relevant_signs=relevant_signs)
    for label, value in flags.items():
        _boolean(value, label)
    control = _condition_text(control_type, "control_type")
    result.update(conditions={**counts, **flags, "control_type": control},
                  rule_strength="general_recommendation_with_exceptions", exception_provisions="not_applicable",
                  connection_changes_authorized=False,
                  source_qualifiers=dict(ordinary_continuity="usually", emphasis="especially_without_traffic_signals",
                                         signal_lane_addition="may_be_useful"))
    if not _context(result, context, RAST):
        return result
    missing = [key for key, value in {**counts, "control_type": control,
               "through_lane_becomes_turn_only": through_lane_becomes_turn_only}.items() if value is None]
    if missing:
        _stop(result, "review_required", "missing_conditions:" + ",".join(missing))
        return result
    if control not in {"traffic_signals", "priority_signs", "right_before_left", "uncontrolled"}:
        _stop(result, "not_applicable", "control_type_outside_this_check_scope")
        return result
    if upstream_through_lane_count == 0:
        _stop(result, "not_applicable", "no_upstream_through_lane")
        return result
    if junction_through_lane_count != upstream_through_lane_count:
        result["reasons"].append(
            "signal_controlled_additional_through_lanes_may_be_useful"
            if junction_through_lane_count > upstream_through_lane_count and control == "traffic_signals"
            else "through_lane_count_change_requires_design_review")
    if through_lane_becomes_turn_only:
        provisions = (early_unambiguous_markings, relevant_signs)
        result["exception_provisions"] = "not_met" if False in provisions else "unknown" if None in provisions else "met"
        result["reasons"].append({
            "not_met": "through_to_turn_lacks_early_markings_or_relevant_signs",
            "unknown": "through_to_turn_exception_evidence_incomplete",
            "met": "through_to_turn_exception_provisions_present_requires_design_review",
        }[result["exception_provisions"]])
    if not result["reasons"]:
        result.update(status="pass", reasons=["ordinary_through_lane_continuity_conditions_met"])
    return result


def check_plan_marking_for_connection(
    *, source, page, marking_id, marking_status=None, legend_verified=None,
):
    """Exclude plan-deletion marks from connection evidence, not from actual legal movements."""
    if not isinstance(source, Mapping):
        raise ValueError("source must contain PDF identity and document metadata.")
    path = Path(_text(source.get("path"), "source.path")).expanduser().resolve(strict=True)
    digest = source.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise ValueError("source requires a valid SHA-256.")
    if file_sha256(path) != digest.lower():
        raise ValueError("Source PDF SHA-256 does not match.")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError("source.path must reference a PDF file.")
    title = _text(source.get("title"), "source.title")
    kind = source.get("document_kind")
    if not isinstance(kind, str) or kind not in _DOCUMENT_KINDS:
        raise ValueError("Use a supported document_kind.")
    if "document_date" not in source:
        raise ValueError("document_date is required. Use null for an unknown date.")
    stamp = _date(source["document_date"], "source.document_date")
    if type(page) is not int or page < 1:
        raise ValueError("page must be a one-based positive integer.")
    marking_id = _text(marking_id, "marking_id")
    status = _condition_text(marking_status, "marking_status")
    if status not in {None, "removed", "retained", "proposed"}:
        raise ValueError("marking_status must be removed, retained, proposed, unknown, or null.")
    _boolean(legend_verified, "legend_verified")
    identity = {**source, "path": str(path), "sha256": digest.lower(), "title": title, "document_date": stamp}
    excluded = status == "removed" and legend_verified is True
    return dict(
        rule_id="plan.removed_marking.connection_evidence.v1", rule_kind="engineering_plan_interpretation",
        status="pass" if excluded else "review_required", decision="review_required",
        marking_id=marking_id, marking_status=status or "unknown", legend_verified=legend_verified,
        connection_evidence="excluded" if excluded else "candidate_requires_review" if legend_verified and status else "unknown",
        sources=[dict(source=identity, pdf_page=page, marking_id=marking_id)],
        field_status="not_verified", page_bounds_verified=False, turn_prohibition_inferred=False,
        geometry_changes_authorized=False, connection_changes_authorized=False,
        reasons=["removed_plan_marking_cannot_support_connection" if excluded else "plan_marking_needs_other_connection_evidence"],
        claim_boundary="This is a plan-interpretation rule, not a RASt clause. Removed means marked for deletion in this plan. "
                       "It does not prove removal on site or prohibit a turn. Retained and proposed markings do not establish "
                       "a complete or current legal connection. The caller supplies the verified legend interpretation.",
    )
