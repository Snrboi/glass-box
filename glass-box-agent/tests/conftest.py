import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"


@pytest.fixture
def policy_cases():
    return json.loads((FIXTURES / "policy-cases.json").read_text())


@pytest.fixture
def services(tmp_path):
    from app.main import Services
    s = Services(data_dir=tmp_path / "data", phase_delay_ms=0)
    yield s
    s.store.close()


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    app = create_app(data_dir=tmp_path / "apidata", phase_delay_ms=0)
    with TestClient(app) as c:
        yield c
