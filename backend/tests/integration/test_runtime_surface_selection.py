from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_PATHS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "backend" / "README.md",
    REPO_ROOT / "AGENTS.md",
]


def test_runtime_surface_contract_is_documented_and_not_split_brain():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/agent/discover" in paths
    assert any(path.startswith("/api/jobs") for path in paths)
    assert not any(path.startswith("/api/runs") for path in paths)
    assert not any(path.startswith("/api/universes") for path in paths)
    assert not any(path.startswith("/api/multiverse/") for path in paths)

    docs_text = "\n".join(path.read_text() for path in DOC_PATHS)
    assert "transitional compatibility" not in docs_text.lower()
    assert "remains documented" not in docs_text.lower()
