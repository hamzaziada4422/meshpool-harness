"""Context windows of models served by the Meshpool proxy that its /v1/models list leaves without max_input_tokens
(or when the list is unreachable): the name table must not fall back to 200k for a 128k model."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meshpool_harness.core import context as C  # noqa: E402


@pytest.mark.parametrize("model,want", [
    ("gpt-oss-120b", 131072), ("openai/gpt-oss-20b", 131072),
    ("grok-4.7", 256000), ("grok-4-fast", 2000000), ("grok-3", 131072),
    ("meshpool-fast", 128000), ("meshpool-balanced", 200000), ("meshpool-max", 160000),
    ("glm-5.3", 200000), ("glm-4.7", 200000), ("kimi-k3", 1048576),
])
def test_table_window(model, want):
    assert C._name_hint(model) == want
