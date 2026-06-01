from pathlib import Path


def test_database_backup_endpoint_streams_dump_file(client, auth_headers, monkeypatch, tmp_path):
    backup_path = tmp_path / "backup.dump"
    backup_path.write_bytes(b"PGDMP\x01")

    monkeypatch.setattr(
        "app.api.endpoints.sources.create_database_backup",
        lambda: (backup_path, "demo-backup.dump"),
    )

    response = client.get("/api/v1/sources/database/backup", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert "demo-backup.dump" in response.headers["content-disposition"]
    assert response.content == b"PGDMP\x01"


def test_database_import_endpoint_accepts_dump_upload(client, auth_headers, monkeypatch):
    captured: dict[str, str] = {}
    reload_triggered: dict[str, bool] = {"value": False}

    def _fake_restore(path: Path) -> None:
        captured["suffix"] = path.suffix
        captured["content"] = path.read_text()

    def _fake_reload() -> None:
        reload_triggered["value"] = True

    monkeypatch.setattr(
        "app.api.endpoints.sources.restore_database_backup",
        _fake_restore,
    )
    monkeypatch.setattr(
        "app.api.endpoints.sources.schedule_backend_reload",
        _fake_reload,
    )

    response = client.post(
        "/api/v1/sources/database/import",
        files={"file": ("demo.dump", b"PGDMP\x01", "application/octet-stream")},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["restart_scheduled"] is True
    assert captured["suffix"] == ".dump"
    assert reload_triggered["value"] is True


def test_database_import_endpoint_rejects_non_dump_upload(client, auth_headers):
    response = client.post(
        "/api/v1/sources/database/import",
        files={"file": ("demo.sql", b"SELECT 1;\n", "application/sql")},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only .dump backup files are supported."
