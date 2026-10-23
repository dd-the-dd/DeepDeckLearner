from __future__ import annotations

import ast
import re
from pathlib import Path

from deepdeck_examples.strategic_graph import ARCHETYPE_WEIGHTS, ARCHETYPES, STRATEGY_NODES


def test_html_diagram_lists_the_actual_nodes_and_archetype_weights() -> None:
    html = (
        Path(__file__).resolve().parents[1] / "apps/learner-web/public/assets/strategy-graphs.html"
    ).read_text(encoding="utf-8")
    literal = re.search(r"const nodes=(\[.*?\]);", html)
    assert literal is not None
    rows = ast.literal_eval(literal[1])
    assert [row[0] for row in rows] == [node.id for node in STRATEGY_NODES]
    for identifier, _label, weights in rows:
        assert weights == [ARCHETYPE_WEIGHTS[archetype][identifier] for archetype in ARCHETYPES]
    assert len(re.findall(r'data-model="', html)) == 5
    assert "<script src=" not in html and '<link rel="stylesheet"' not in html
