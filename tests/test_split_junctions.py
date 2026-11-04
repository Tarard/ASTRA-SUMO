from pathlib import Path

from astra_sumo.core.split_junctions import (
    NetView,
    cluster_id,
    osm_turn_restrictions,
    read_net,
    restriction_changes,
    split_junction_groups,
)


def _edge(edge_id: str, start: str, end: str, length: float, allow: str = "") -> str:
    lane = f'<lane id="{edge_id}_0" index="0" length="{length}"{f" allow={chr(34)}{allow}{chr(34)}" if allow else ""}/>'
    return f'<edge id="{edge_id}" from="{start}" to="{end}">{lane}</edge>'


def _split_net(path: Path, *, inner_m: float = 0.2) -> Path:
    """Two halves A and B of one junction, 12 m apart, linked by an inner edge; arms 1-4 and a side node S at B."""
    junctions = {"A": (0, 0), "B": (12, 0), "1": (-100, 0), "2": (0, 100), "3": (112, 0), "4": (12, -100),
                 "S": (12, 100)}
    edges = [_edge("a1", "1", "A", 100), _edge("a1r", "A", "1", 100), _edge("a2", "2", "A", 100),
             _edge("a2r", "A", "2", 100), _edge("b3", "B", "3", 100), _edge("b3r", "3", "B", 100),
             _edge("b4", "4", "B", 100), _edge("b4r", "B", "4", 100), _edge("bs", "B", "S", 100),
             _edge("ab", "A", "B", inner_m), _edge("ba", "B", "A", inner_m)]
    text = "".join(edges) + "".join(f'<junction id="{node}" type="priority" x="{x}" y="{y}"/>'
                                    for node, (x, y) in junctions.items())
    text += ('<connection from="a1" to="ab" fromLane="0" toLane="0"/><connection from="ab" to="b3" fromLane="0" '
             'toLane="0"/><connection from="ab" to="b4r" fromLane="0" toLane="0"/>')
    path.write_text(f"<net>{text}</net>", encoding="utf-8")
    return path


def test_split_junction_groups_join_halves_linked_by_a_swallowed_edge(tmp_path: Path) -> None:
    view = read_net(_split_net(tmp_path / "net.xml"))
    found = split_junction_groups(view)

    assert (found["linker_edge_count"], [row["nodes"] for row in found["groups"]]) == (2, [["A", "B"]])
    assert found["groups"][0]["span_m"] == 12.0
    assert view.passenger_links == {"a1": {"ab"}, "ab": {"b3", "b4r"}}
    # A longer inner edge links nothing.
    assert split_junction_groups(read_net(_split_net(tmp_path / "long.xml", inner_m=5.0)))["groups"] == []
    assert split_junction_groups(view, excluded={"B"})["skipped"][0]["reason"] == "excluded_after_check"


def test_split_junction_groups_take_in_whole_controllers_and_skip_wide_ones() -> None:
    view = NetView(
        edges={"ab": ("A", "B", [0.2], True, True), **{f"{node}{arm}": (node, f"{node}{arm}x", [50.0], True, True)
                                                       for node in "ABC" for arm in "123"}},
        junctions={"A": ("traffic_light", 0, 0), "B": ("priority", 10, 0), "C": ("traffic_light", 40, 0)},
        controller_nodes={"SL_A": {"A", "C"}})
    found = split_junction_groups(view)

    # A's controller also signals C, so the group grows to A, B, C and is then 40 m across.
    assert found["groups"] == [] and found["skipped"][0]["nodes"] == ["A", "B", "C"]
    assert found["skipped"][0]["reason"] == "controller_closure_too_wide"
    assert [row["nodes"] for row in split_junction_groups(view, span_m=50.0)["groups"]] == [["A", "B", "C"]]


def test_cluster_id_follows_netconvert() -> None:
    assert cluster_id(["2", "10", "1"]) == "cluster_1_10_2"
    assert cluster_id([str(n) for n in range(6)]) == "cluster_0_1_2_3_#2more"


def _restriction_view(links: dict[str, set[str]]) -> NetView:
    """Way 10 enters half A, ways 30 and 40 leave half B; 25 is the inner edge."""
    edges = {"10#0": ("1", "A", [50.0], True, True), "25": ("A", "B", [0.2], True, True),
             "30#0": ("B", "3", [50.0], True, True), "40#0": ("B", "4", [50.0], True, True)}
    view = NetView(edges=edges, passenger_links=links)
    for edge_id, (start, end, *_rest) in edges.items():
        view.incoming.setdefault(end, []).append(edge_id)
        view.outgoing.setdefault(start, []).append(edge_id)
    return view


def test_restriction_changes_find_a_join_that_lifts_a_ban_or_drops_the_only_turn() -> None:
    base = _restriction_view({"10#0": {"25"}, "25": {"30#0"}})
    ban = [{"id": "1", "restriction": "no_right_turn", "from": "25", "via": "B", "to": "40"}]
    only = [{"id": "2", "restriction": "only_straight_on", "from": "25", "via": "B", "to": "30"}]

    kept = _restriction_view({"10#0": {"30#0"}})
    lifted = _restriction_view({"10#0": {"30#0", "40#0"}})
    lost = _restriction_view({"10#0": set()})
    assert restriction_changes(base, kept, [["A", "B"]], ban + only) == []
    assert [(row["id"], row["status"]) for row in restriction_changes(base, lifted, [["A", "B"]], ban + only)] == [
        ("1", "newly_allowed"), ("2", "newly_allowed")]
    assert [(row["id"], row["status"]) for row in restriction_changes(base, lost, [["A", "B"]], only)] == [
        ("2", "mandatory_turn_lost")]
    # A via node outside every join is not compared.
    assert restriction_changes(base, lifted, [["X", "Y"]], ban) == []


def test_osm_turn_restrictions_keep_relations_with_one_via_node(tmp_path: Path) -> None:
    osm = tmp_path / "input.osm.xml"
    osm.write_text(
        '<osm><relation id="1"><member type="way" ref="10" role="from"/><member type="node" ref="5" role="via"/>'
        '<member type="way" ref="20" role="to"/><tag k="type" v="restriction"/><tag k="restriction" v="no_left_turn"/>'
        '</relation><relation id="2"><member type="way" ref="10" role="from"/><member type="way" ref="11" role="via"/>'
        '<member type="way" ref="20" role="to"/><tag k="type" v="restriction"/><tag k="restriction" v="no_u_turn"/>'
        '</relation></osm>', encoding="utf-8")

    assert osm_turn_restrictions(osm) == [
        {"id": "1", "restriction": "no_left_turn", "from": "10", "via": "5", "to": "20"}]
