import pytest
from fastapi.testclient import TestClient

from sleepy.backend import create_backend
from sleepy.storage import Store


@pytest.fixture
def backend(tmp_path):
    store = Store(tmp_path)
    password = store.initialize()
    store.close()
    app = create_backend(tmp_path, "test-internal-token")
    with TestClient(app, base_url="http://testserver") as client:
        yield client, app, password, tmp_path


def sign_in(client, password):
    auth = client.get("/api/admin/auth").json()
    headers = {"Origin": "http://testserver", "X-CSRF-Token": auth["csrf"]}
    result = client.post("/api/admin/login", json={"password": password}, headers=headers)
    assert result.status_code == 200
    headers["X-CSRF-Token"] = result.json()["csrf"]
    return headers


@pytest.fixture
def admin_client(backend):
    client, app, initial, path = backend
    headers = sign_in(client, initial)
    result = client.post(
        "/api/admin/password",
        json={"current_password": initial, "new_password": "NewPass123!"},
        headers=headers,
    )
    assert result.status_code == 204
    headers = sign_in(client, "NewPass123!")
    return client, app, headers, path
