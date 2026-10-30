from __future__ import annotations

import io
import xml.etree.ElementTree as ET

import pytest

from astra_sumo.core.xml_stream import DirectChildren, read_blocks


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
