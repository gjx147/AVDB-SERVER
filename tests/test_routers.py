"""测试 router 层 API 端点。"""


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_ready(client):
    r = client.get("/api/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_tasks_empty(client):
    r = client.get("/api/tasks")
    assert r.status_code == 200
    assert "total" in r.json()


def test_tasks_stats(client):
    r = client.get("/api/tasks/stats")
    assert r.status_code == 200


def test_rankings_dates(client):
    r = client.get("/api/rankings/types/dates")
    assert r.status_code == 200


def test_status_stats(client):
    r = client.get("/api/status/stats")
    assert r.status_code == 200


def test_dashboard_stats(client):
    r = client.get("/api/dashboard/stats")
    assert r.status_code == 200
    assert "tasks" in r.json()


def test_v2_analytics(client):
    r = client.get("/api/v2/analytics")
    assert r.status_code == 200
    assert "rating_dist" in r.json()


def test_system_disk(client):
    r = client.get("/api/system/disk")
    assert r.status_code == 200


def test_settings_get(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
