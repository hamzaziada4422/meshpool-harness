"""Review, 0.0.18: two-word commands whose second word is the verb. A grant for `docker compose ps` was
"docker compose", which then covered `docker compose exec web sh -c ...`; `uv tool run` is uvx, but its grant was
"uv tool", which covered `uv tool run bash -c "rm -rf ~"`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meshpool_harness.core.permrules import grant_patterns as g, granted  # noqa: E402


def test_docker_compose_and_container_grants_name_the_verb():
    assert g("docker compose ps") == ["docker compose ps"]
    assert not granted("docker compose exec web sh -c 'rm -rf /'", g("docker compose ps"))
    assert not granted("docker compose run web sh", g("docker compose ps"))
    assert g("docker compose exec web sh") == [] and g("docker compose run web sh") == []
    assert g("docker container ls") == ["docker container ls"]
    assert not granted("docker container exec x sh", g("docker container ls"))
    assert g("docker container exec x sh") == []
    assert g("docker compose -f a.yml ps") == []                     # an option before the verb: not judged


def test_uv_tool_run_is_judged_like_uvx():
    assert g("uv tool run ruff check .") == ["uv tool run ruff"]
    assert not granted('uv tool run bash -c "rm -rf ~"', g("uv tool run ruff"))
    assert g("uv tool run -q ruff") == []
    assert g("uv tool install ruff") == ["uv tool install"]
    assert g("uv run pytest") == ["uv run pytest"] and g("docker ps") == ["docker ps"]   # unchanged
