import copy
import json
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest
from web_fixture import fake_remote

from napback import core
from napback.webapp import (
    Application,
    Server,
    analyze,
    connection,
    sources_from_selection,
)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(core.Config, "remote", fake_remote)
    app = Application(tmp_path / "config.json")
    app.connect({"host": "backup@nas", "key": "", "sudo": True})
    return app


def settings(app):
    return {
        "selected": ["Vault", "Vault/photos"],
        "target": str(app.config_path.parent / "backup"),
        "storage": "zfs_raw",
        "check_interval_minutes": 3,
        "keep": 30,
        "automatic": False,
    }


def test_discovery_real_names_and_missing_child_are_visible(app):
    catalog = app.catalog
    assert catalog["datasets"][0]["snapshots"][0]["name"] == "auto-2"
    source = [{"name": "files", "dataset": "Vault", "recursive": True, "exclude": ["Vault/disk"]}]
    report = analyze(catalog, source)
    assert "Vault/missing" in " ".join(report["issues"])
    assert report["groups"][0]["examples"][0]["name"] == "auto-2"


def test_explicit_exclusions_and_reincluded_subtree(app):
    selection = sources_from_selection(app.catalog, ["Vault", "Vault/photos"])
    assert selection[0]["exclude"] == ["Vault/disk", "Vault/missing"]
    report = analyze(app.catalog, selection)
    assert not report["issues"] and report["count"] == 2
    assert report["groups"][0]["snapshot"]["name"] == "auto-2"
    child = copy.deepcopy(app.catalog["datasets"][1])
    child["name"] = "Vault/missing/reincluded"
    app.catalog["datasets"].append(child)
    sources = sources_from_selection(app.catalog, ["Vault", "Vault/missing/reincluded"])
    assert len(sources) == 2
    assert analyze(app.catalog, sources)["count"] == 2


def test_stale_and_mixed_encryption_sources_do_not_get_a_ready_label(app):
    source = sources_from_selection(app.catalog, ["Other"])
    assert "nicht verschlüsselt" in " ".join(analyze(app.catalog, source)["issues"])
    assert not analyze(app.catalog, source, storage="files")["issues"]
    assert "älter als zwei Tage" in " ".join(
        analyze(app.catalog, source, storage="files", now=time.time() + 200000)["issues"]
    )


def test_review_and_save_require_validated_snapshot_plan_and_write_real_config(app):
    draft = settings(app)
    with pytest.raises(core.BackupError, match="Zusammenfassung"):
        app.save({"review_id": "unreviewed"})
    review = app.review_settings(draft)
    assert review["ok"] and not app.config_path.exists()
    with patch("napback.webapp.subprocess.run"):
        result = app.save({"review_id": review["review_id"]})
    assert result["saved"]
    config = core.Config.load(app.config_path)
    core.check_repository(config)
    assert config.snapshot_prefix == "" and config.check_interval_minutes == 3
    assert config.sources[0]["exclude"] == ["Vault/disk", "Vault/missing"]
    plan = core.plan_sources(config, time.time())
    assert {s.dataset for s in plan} == {"Vault", "Vault/photos"}
    with pytest.raises(core.BackupError, match="Zusammenfassung"):
        app.save({"review_id": review["review_id"]})


def test_failed_review_never_creates_target_or_replaces_existing_config(app):
    draft = settings(app)
    draft["selected"].append("Vault/missing")
    result = app.review_settings(draft)
    assert not result["ok"]
    assert not app.config_path.exists() and not (app.config_path.parent / "backup").exists()


def test_config_edit_since_review_is_not_overwritten(app):
    review = app.review_settings(settings(app))
    app.config_path.write_text("concurrent user edit")
    with pytest.raises(core.BackupError, match="inzwischen geändert"):
        app.save({"review_id": review["review_id"]})
    assert app.config_path.read_text() == "concurrent user edit"


def test_config_edit_during_remote_recheck_is_not_overwritten(app):
    review = app.review_settings(settings(app))
    plan = core.plan_sources

    def changed_during_recheck(*args):
        result = plan(*args)
        app.config_path.write_text("edit during SSH check")
        return result

    with patch.object(core, "plan_sources", changed_during_recheck):
        with pytest.raises(core.BackupError, match="während der NAS-Prüfung"):
            app.save({"review_id": review["review_id"]})
    assert app.config_path.read_text() == "edit during SSH check"
    assert not (app.config_path.parent / "backup").exists()


def test_relative_destination_never_depends_on_server_working_directory(app):
    draft = settings(app)
    draft["target"] = "some-relative-folder"
    sources, storage = app.selection(draft)
    with pytest.raises(core.BackupError, match="vollständigen Pfad"):
        app.settings(draft, sources, storage)


def test_config_edits_create_backup_and_preserve_repository_identity(app):
    draft = settings(app)
    with patch("napback.webapp.subprocess.run"):
        review = app.review_settings(draft)
        app.save({"review_id": review["review_id"]})
        before = core.read_json(app.config_path)
        draft["check_interval_minutes"] = 7
        review = app.review_settings(draft)
        app.save({"review_id": review["review_id"]})
    after = core.read_json(app.config_path)
    assert after["repository_id"] == before["repository_id"]
    assert after["sources"] == before["sources"]
    assert after["check_interval_minutes"] == 7
    backup = list(app.config_path.parent.glob("config.json.bak-*"))
    assert len(backup) == 1 and core.read_json(backup[0]) == before


def test_dataset_changes_force_a_new_selection(app):
    with patch(
        "napback.webapp.discover",
        return_value={**app.catalog, "datasets": app.catalog["datasets"][:-1]},
    ):
        with pytest.raises(core.BackupError, match="angelegt oder entfernt"):
            app.review_settings(settings(app))


@pytest.mark.parametrize(
    "excluded", [["Vault"], ["Other/photos"], ["Vault/../Other"], "Vault/photos"]
)
def test_core_rejects_invalid_exclusions(tmp_path, excluded):
    data = core.initialize(tmp_path / "repository", storage="zfs_raw")
    data.update(host="nas", sources=[{"name": "files", "dataset": "Vault", "exclude": excluded}])
    path = tmp_path / "config.json"
    core.write_json(path, data)
    with pytest.raises(core.BackupError, match="exclude"):
        core.Config.load(path)


def test_http_auth_origin_host_and_readonly_routes(app):
    server = Server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, token=None, origin=None, host=None, data=None):
        headers = {}
        if token is not None:
            headers["X-Napback-Token"] = token
        if origin is not None:
            headers["Origin"] = origin
        if host is not None:
            headers["Host"] = host
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        return urllib.request.urlopen(
            urllib.request.Request(server.origin + path, data=body, headers=headers), timeout=5
        )

    try:
        with request("/") as r:
            assert (
                r.status == 200 and "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
            )
        for kwargs in [
            {},
            {"token": "wrong"},
            {"token": server.token, "origin": "https://evil.invalid"},
            {"token": server.token, "host": "evil.invalid"},
        ]:
            with pytest.raises(urllib.error.HTTPError) as error:
                request("/api/initial", **kwargs)
            assert error.value.code == 403
        with request("/api/initial", token=server.token) as r:
            assert json.load(r)["configured"] is False
        with pytest.raises(urllib.error.HTTPError) as error:
            request("/api/save", token=server.token)
        assert error.value.code == 404
        with request(
            "/api/preview", token=server.token, origin=server.origin, data=settings(app)
        ) as r:
            job = json.load(r)["job"]
        for _ in range(50):
            with request("/api/jobs/" + job, token=server.token) as r:
                state = json.load(r)
            if state["state"] != "running":
                break
            time.sleep(0.02)
        assert state["state"] == "done" and state["result"]["count"] == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_unsafe_connections_rejected_and_volumes_need_raw_storage(app):
    with pytest.raises(ValueError):
        connection({"host": "nas; touch /tmp/owned"})
    selection = sources_from_selection(app.catalog, ["Vault/disk"])
    assert not analyze(app.catalog, selection)["issues"]
    assert "Virtuelle Festplatten" in " ".join(analyze(app.catalog, selection, "files")["issues"])


def test_setup_opens_browser_by_default(tmp_path):
    from napback.cli import main

    path = tmp_path / "config.json"
    with patch("napback.webapp.open_ui", return_value={"status": "web_opened"}) as opened:
        assert main(["--config", str(path), "setup"]) == 0
    opened.assert_called_once_with(path)


def test_custom_ui_jobs_never_disable_the_default_timer(app):
    draft = settings(app)
    review = app.review_settings(draft)
    with patch("napback.webapp.subprocess.run") as run:
        run.return_value.returncode = 0
        app.save({"review_id": review["review_id"]})
    commands = [call.args[0] for call in run.call_args_list]
    assert not any("napback.timer" in command for command in commands)
    assert any(app.timer_unit() + ".timer" in command for command in commands)


def test_unencrypted_virtual_disk_never_recommends_file_mode(app):
    disk = next(d for d in app.catalog["datasets"] if d["name"] == "Vault/disk")
    disk["encrypted"] = False
    sources = sources_from_selection(app.catalog, ["Vault/disk"])
    issues = " ".join(analyze(app.catalog, sources)["issues"])
    assert "Unverschlüsselte virtuelle Festplatten" in issues
    assert "Dateikopie" not in issues
