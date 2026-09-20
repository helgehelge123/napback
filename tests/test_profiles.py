from unittest.mock import patch

import pytest
from web_fixture import fake_remote

from napback import core
from napback.profiles import Profiles
from napback.webapp import Application


def save(app, target, storage="zfs_raw"):
    app.connect({"host": "backup@nas", "key": "", "sudo": True})
    review = app.review_settings(
        dict(
            selected=["Vault/photos"],
            target=str(target),
            storage=storage,
            automatic=False,
            label="Private photos" if storage == "zfs_raw" else "Readable copy",
        )
    )
    assert review["ok"]
    with patch("napback.webapp.subprocess.run"):
        app.save({"review_id": review["review_id"]})
    return core.Config.load(app.config_path)


def test_duplicate_has_independent_identity_destination_storage_and_timer(tmp_path, monkeypatch):
    monkeypatch.setattr(core.Config, "remote", fake_remote)
    original = Application(tmp_path / "config.json")
    profiles = Profiles(original)
    first = save(original, tmp_path / "encrypted")
    before = original.config_path.read_bytes()
    ident = profiles.create("default")["profile"]
    duplicate = profiles.get(ident)
    initial = duplicate.initial()
    assert not initial["configured"] and initial["defaults"]["target"] == ""
    assert initial["defaults"]["automatic"] is False
    assert initial["defaults"]["selected_sources"] == first.sources
    with pytest.raises(core.BackupError, match="eigenen Ordner"):
        save(duplicate, first.target, storage="files")
    with pytest.raises(core.BackupError, match="überschneidet"):
        save(duplicate, first.target / "nested", storage="files")
    second = save(duplicate, tmp_path / "readable", storage="files")
    assert second.repository_id != first.repository_id
    assert first.storage == "zfs_raw" and second.storage == "files"
    assert original.timer_unit() != duplicate.timer_unit()
    assert original.config_path.read_bytes() == before
    # All saved jobs survive a new server without an in-memory registry.
    reopened = Profiles(Application(original.config_path))
    assert {x["id"] for x in reopened.listing()} == {"default", ident}
    assert reopened.get(ident).initial()["defaults"]["storage"] == "files"


def test_profile_ids_and_symlinks_cannot_select_arbitrary_configs(tmp_path):
    profiles = Profiles(Application(tmp_path / "config.json"))
    for ident in ["../other", str(tmp_path / "secret"), "default.json", "a" * 32]:
        with pytest.raises(core.BackupError):
            profiles.get(ident)
    profiles.directory.mkdir()
    path = profiles.directory / ("b" * 32 + ".json")
    path.symlink_to(tmp_path / "secret")
    with pytest.raises(core.BackupError, match="Symlink"):
        profiles.get("b" * 32)


def test_new_job_inherits_connection_but_not_data_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(core.Config, "remote", fake_remote)
    original = Application(tmp_path / "config.json")
    profiles = Profiles(original)
    save(original, tmp_path / "encrypted")
    new = profiles.get(profiles.create()["profile"]).initial()["defaults"]
    assert new["host"] == "backup@nas" and new["selected_sources"] == []
    assert not new["backup_truenas_config"] and not new["automatic"]


def test_changed_other_target_is_rechecked_at_save(tmp_path, monkeypatch):
    monkeypatch.setattr(core.Config, "remote", fake_remote)
    app = Application(tmp_path / "config.json")
    profiles = Profiles(app)
    save(app, tmp_path / "original")
    second = profiles.get(profiles.create("default")["profile"])
    second.connect({"host": "backup@nas", "key": ""})
    review = second.review_settings(
        dict(selected=["Vault/photos"], target=str(tmp_path / "second"), storage="files")
    )
    data = core.read_json(app.config_path)
    data["target"] = str(tmp_path / "second")
    core.write_json(app.config_path, data)
    with pytest.raises(core.BackupError, match="überschneidet"):
        second.save({"review_id": review["review_id"]})
    assert not second.config_path.exists()


def test_copy_preserves_advanced_transport_options(tmp_path, monkeypatch):
    monkeypatch.setattr(core.Config, "remote", fake_remote)
    app = Application(tmp_path / "config.json")
    profiles = Profiles(app)
    save(app, tmp_path / "first")
    data = core.read_json(app.config_path)
    data.update(ssh_options=["-p", "2222"], io_timeout_seconds=300, bandwidth_limit_kib=1000)
    core.write_json(app.config_path, data)
    copied = profiles.get(profiles.create("default")["profile"])
    result = save(copied, tmp_path / "copy")
    assert result.ssh_options == ["-p", "2222"]
    assert result.io_timeout_seconds == 300 and result.bandwidth_limit_kib == 1000


def test_http_profile_headers_isolate_review_jobs_and_key_download(tmp_path, monkeypatch):
    import json
    import threading
    import urllib.error
    import urllib.request

    from napback.webapp import Server

    monkeypatch.setattr(core.Config, "remote", fake_remote)
    app = Application(tmp_path / "config.json")
    server = Server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(route, profile="default", payload=None, token=True):
        headers = {"X-Napback-Profile": profile}
        if token:
            headers["X-Napback-Token"] = server.token
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        return urllib.request.urlopen(
            urllib.request.Request(server.origin + "/api/" + route, data=data, headers=headers),
            timeout=10,
        )

    try:
        with request("profiles", payload={"copy": False}) as response:
            second = json.load(response)["profile"]
        with request("initial", profile=second) as response:
            assert json.load(response)["profile"] == second
        review = {
            "token": "only-first-profile",
            "time": __import__("time").time(),
            "old_hash": None,
        }
        app.review = review
        with pytest.raises(core.BackupError, match="Zusammenfassung"):
            server.profiles.get(second).save({"review_id": review["token"]})
        for method in [None, {}]:
            with pytest.raises(urllib.error.HTTPError) as error:
                request("recovery-key", payload=method, token=False)
            assert error.value.code == 403
        assert not app.key_path().exists()
        with request("recovery-key", payload={}) as response:
            key = response.read()
            assert b"\n" in key and len(key.strip()) == 44
        with request("recovery-key", profile=second, payload={}) as response:
            assert response.read() == key
        with pytest.raises(urllib.error.HTTPError):
            request("initial", profile="../private")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
