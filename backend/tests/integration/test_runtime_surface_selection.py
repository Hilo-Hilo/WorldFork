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

    runs_paths = [path for path in paths if path.startswith("/api/runs")]
    docs_text = "\n".join(path.read_text() for path in DOC_PATHS)

    if runs_paths:
        assert "/api/runs" in docs_text
        assert "transitional" in docs_text.lower()
        assert "canonical" in docs_text.lower()
    else:
        assert "/api/runs" in docs_text
        assert "transitional" in docs_text.lower()
        assert "canonical" in docs_text.lower()
