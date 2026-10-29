"""Settings → Cities: icp.geography / icp.geo_coordinates edited from the dashboard."""

import shutil

import pytest
from fastapi.testclient import TestClient

import harvey.dashboard as dash
from harvey.config import load_config
from harvey.paths import PROJECT_ROOT


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    cfg = tmp_path / "harvey.yaml"
    shutil.copy(PROJECT_ROOT / "harvey.yaml", cfg)
    monkeypatch.setattr(dash, "_resolve_config_file", lambda: cfg)
    monkeypatch.setattr(dash, "PID_FILE", tmp_path / "data" / "harvey.pid")
    dash.token_store.configure(tmp_path / "data" / "dashboard-token")
    with TestClient(dash.app, base_url="http://127.0.0.1:5555",
                    headers={"Authorization": f"Bearer {dash.token_store.get()}"}) as c:
        c.cfg = cfg
        yield c


def test_cities_round_trip_and_keep_comments(client):
    before = client.cfg.read_text(encoding="utf-8")
    r = client.post("/api/settings/geography", json={"cities": [
        {"name": "Curitiba, PR", "coordinate": "-25.4284, -49.2733, 50"},
        {"name": "São José dos Pinhais, PR"},
    ]})
    assert r.status_code == 200 and r.json()["success"]

    icp = load_config(str(client.cfg)).icp
    assert icp.geography == ["Curitiba, PR", "São José dos Pinhais, PR"]
    assert icp.geo_coordinates == {"Curitiba, PR": "-25.4284,-49.2733,50"}
    assert client.get("/api/settings/geography").json()["cities"][1] == {
        "name": "São José dos Pinhais, PR", "coordinate": ""}

    after = client.cfg.read_text(encoding="utf-8")
    comments = lambda t: [l for l in t.splitlines() if l.strip().startswith("#")]
    assert comments(after) == comments(before)
    assert icp.titles == load_config(str(PROJECT_ROOT / "harvey.yaml")).icp.titles

    # Saving again replaces the blocks rather than stacking them.
    client.post("/api/settings/geography", json={"cities": [{"name": "Londrina, PR"}]})
    icp = load_config(str(client.cfg)).icp
    assert icp.geography == ["Londrina, PR"] and icp.geo_coordinates == {}


@pytest.mark.parametrize("cities", [
    [], "Curitiba", [{"name": ""}], [{"name": "A"}, {"name": "a"}],
    [{"name": "A", "coordinate": "north"}], [{"name": "A", "coordinate": "1,2\nicp: x"}],
])
def test_bad_input_is_refused_and_the_file_untouched(client, cities):
    before = client.cfg.read_text(encoding="utf-8")
    r = client.post("/api/settings/geography", json={"cities": cities})
    assert r.status_code == 409 and not r.json()["success"]
    assert client.cfg.read_text(encoding="utf-8") == before
