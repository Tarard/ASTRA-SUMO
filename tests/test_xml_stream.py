from __future__ import annotations

import gc
import io
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.xml_stream import (
    DirectChildren,
    binary_blocks,
    compact_tree,
    read_blocks,
    rewrite_indented,
    text_blocks,
)


DOCUMENT = b"""<?xml version="1.0" encoding="UTF-8"?>
<net version="1.20" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="n.xsd">
    <!-- a comment between children -->
    <edge id="a"><lane id="a_0" allow="pedestrian"><param key="k" value="x &amp; y"/></lane></edge>
    text after a <?pi kept out?>
    <junction id="j" type="priority" x="1" y="2"/>
    <connection from="a" to="a"/>
</net>
"""


@pytest.mark.parametrize("size", [1, 2, 7, 64, 1 << 16])
def test_direct_children_serialize_like_a_parsed_tree_for_any_block_size(size):
    tree = ET.fromstring(DOCUMENT)
    stream = DirectChildren(read_blocks(io.BytesIO(DOCUMENT), size))

    streamed = [ET.tostring(child, encoding="utf-8") for child in stream]

    assert streamed == [ET.tostring(child, encoding="utf-8") for child in tree]
    assert (stream.root.tag, stream.root.attrib, stream.root.text) == (tree.tag, tree.attrib, tree.text)
    assert len(stream.root) == 0


def test_direct_children_raise_the_parse_error_of_a_whole_document_parse():
    broken = DOCUMENT.replace(b"<junction", b"<<junction")
    with pytest.raises(ET.ParseError) as whole:
        ET.fromstring(broken)
    with pytest.raises(ET.ParseError) as streamed:
        list(DirectChildren(read_blocks(io.BytesIO(broken), 5)))
    assert str(streamed.value) == str(whole.value)


def _tree_rewrite(path: Path, edit) -> None:
    tree = ET.parse(path)
    root = tree.getroot()
    for index, element in enumerate(list(root)):
        kept = edit(index, element)
        if kept is None:
            root.remove(element)
        elif kept is not element:
            root[list(root).index(element)] = kept
    ET.indent(root, space="    ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _edit(index, element):
    if element.tag == "connection":
        return None
    if element.tag == "junction":
        element.set("type", "traffic_light")
    if element.tag == "edge":
        replacement = ET.Element("edge", {"id": "b"})
        replacement.extend(element)
        return replacement
    return element


@pytest.mark.parametrize("edit", [lambda index, element: element, _edit])
def test_indented_rewrite_writes_the_bytes_of_elementtree_indent_and_write(tmp_path, edit):
    streamed, whole = tmp_path / "streamed.net.xml", tmp_path / "whole.net.xml"
    streamed.write_bytes(DOCUMENT)
    whole.write_bytes(DOCUMENT)

    rewrite_indented(streamed, edit)
    _tree_rewrite(whole, edit)

    assert streamed.read_bytes() == whole.read_bytes()
    assert list(tmp_path.glob(".torii-*.tmp")) == []


def test_indented_rewrite_writes_a_namespaced_element_as_one_tree(tmp_path):
    document = DOCUMENT.replace(b'<junction id="j"', b'<junction xsi:type="x" id="j"')
    streamed, whole = tmp_path / "streamed.net.xml", tmp_path / "whole.net.xml"
    streamed.write_bytes(document)
    whole.write_bytes(document)

    rewrite_indented(streamed, _edit)
    _tree_rewrite(whole, _edit)

    assert b'xsi:type="x"' in streamed.read_bytes()
    assert streamed.read_bytes() == whole.read_bytes()


def test_indented_rewrite_leaves_the_file_unchanged_on_a_parse_error(tmp_path):
    broken = tmp_path / "broken.net.xml"
    broken.write_bytes(DOCUMENT.replace(b"</net>", b""))
    with pytest.raises(ET.ParseError):
        rewrite_indented(broken, lambda index, element: element)
    assert broken.read_bytes() == DOCUMENT.replace(b"</net>", b"")
    assert list(tmp_path.glob(".torii-*.tmp")) == []


@pytest.mark.parametrize("size", [3, 1 << 16])
def test_compact_tree_shares_equal_strings_and_leaves_out_unread_parameters(size):
    document = DOCUMENT.replace(b"</net>", b'<edge id="b"><lane id="b_0" allow="pedestrian"/></edge></net>')
    tree = ET.fromstring(document)
    compact = compact_tree(read_blocks(io.BytesIO(document), size), without_params_of={"lane"})

    for element in tree.iter("lane"):
        for parameter in element.findall("param"):
            element.remove(parameter)
    assert ET.tostring(compact) == ET.tostring(tree)
    first, second = compact.iter("lane")
    assert first.get("allow") is second.get("allow")
    assert compact_tree(read_blocks(io.BytesIO(document), size)).find("edge/lane/param") is not None


def _parse_direct_children(blocks):
    return list(DirectChildren(blocks))


def _parse_compact_tree(blocks):
    return compact_tree(blocks)


@pytest.mark.parametrize("parse", [_parse_direct_children, _parse_compact_tree])
@pytest.mark.parametrize("blocks", [binary_blocks, text_blocks])
def test_a_mid_file_parse_error_releases_the_file_at_once(tmp_path, parse, blocks):
    # A parse error raised by the parser sits in a reference cycle; the file must not wait for the collector.
    broken = tmp_path / "broken.net.xml"
    broken.write_bytes(DOCUMENT.replace(b"<junction", b"<<&&>>junction"))
    collecting = gc.isenabled()
    gc.disable()
    try:
        with pytest.raises(ET.ParseError):
            parse(blocks(broken, 7))
        moved = broken.replace(tmp_path / "moved.net.xml")
        moved.unlink()
    finally:
        if collecting:
            gc.enable()
    assert not moved.exists()


@pytest.mark.parametrize("parse", [_parse_direct_children, _parse_compact_tree])
def test_the_block_source_is_closed_when_parsing_fails(parse):
    closed = []

    def blocks():
        try:
            yield from read_blocks(io.BytesIO(DOCUMENT.replace(b"<junction", b"<<junction")), 5)
        finally:
            closed.append(True)

    collecting = gc.isenabled()
    gc.disable()
    try:
        with pytest.raises(ET.ParseError):
            parse(blocks())
        assert closed == [True]
    finally:
        if collecting:
            gc.enable()


def test_stopping_early_closes_the_block_source():
    closed = []

    def blocks():
        try:
            yield from read_blocks(io.BytesIO(DOCUMENT), 5)
        finally:
            closed.append(True)

    children = iter(DirectChildren(blocks()))
    assert next(children).tag == "edge"
    children.close()
    assert closed == [True]
