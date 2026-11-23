"""Print the requirements the bundled Hermes needs: its dependencies plus the acp, mcp and anthropic extras (the
last for a key endpoint that speaks Anthropic Messages: harnesses.prepare_hermes sets api_mode anthropic_messages).
Hermes refuses to be built as a wheel; Meshpool runs it from source (app/vendor/hermes), so only its dependencies
are installed into the bundled Python. Usage: python hermes_requirements.py path/to/hermes-agent > req.txt"""
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:          # Python 3.10
    import tomli as tomllib

p = tomllib.loads((Path(sys.argv[1]) / "pyproject.toml").read_text(encoding="utf-8"))["project"]
extras = p.get("optional-dependencies") or {}
for r in [*p.get("dependencies", []), *extras.get("acp", []), *extras.get("mcp", []), *extras.get("anthropic", [])]:
    print(r)
