from unittest.mock import patch

import pytest

from napback import core
from napback.cli import main
from napback.wizard import Guide


def test_setup_saves_explicit_key_prefix_and_retries_invalid_input(tmp_path, monkeypatch, capsys):
    key = tmp_path / "private key"
    # A synthetic placeholder; the wizard must only handle its path, never read it.
    key.write_text("DO NOT DISPLAY THIS KEY CONTENT")
    public = tmp_path / "key.pub"
    public.write_text("public placeholder")
    target = tmp_path / "backup"
    config_path = tmp_path / "config.json"
    answers = iter(
        [
            "?",
            "https://nas.local/",
            "backup@nas.local",
            "?",
            str(public),
            str(key),
            "vielleicht",
            "ja",
            "tank/documents",
            "autosnap_",
            str(target),
            "?",
            "1",
            "0",
            "5",
            "nein",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    observed = []

    def remote(config, _args):
        observed.append(config.ssh_options)
        return "tank/documents\t1M\taes-256-gcm\n"

    with (
        patch.object(core.Config, "remote", remote),
        patch.object(core, "plan_sources", return_value=[]),
    ):
        assert main(["--config", str(config_path), "setup", "--terminal"]) == 0
    config = core.Config.load(config_path)
    assert config.ssh_options == ["-i", str(key), "-oIdentitiesOnly=yes"]
    assert observed == [config.ssh_options]
    assert config.snapshot_prefix == "autosnap_" and config.check_interval_minutes == 5
    output = capsys.readouterr().out
    assert "DO NOT DISPLAY THIS KEY CONTENT" not in output
    assert "SSH ist die verschlüsselte Verbindung" in output
    assert "Bitte korrigieren" in output
    assert "Public SSH Key" in output


def test_failed_ssh_shows_actionable_help_and_never_writes_config(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    answers = iter(["backup@nas", "", "j"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    with patch.object(
        core.Config, "remote", side_effect=core.BackupError("Host key verification failed")
    ):
        assert main(["--config", str(config), "setup", "--terminal"]) == 1
    message = capsys.readouterr().err
    assert "System > Services > SSH" in message
    assert "ssh -oStrictHostKeyChecking=ask backup@nas true" in message
    assert "Fingerabdruck" in message
    assert not config.exists()


def test_english_setup_remains_available(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    answers = iter(
        ["nas", "", "yes", "tank/documents", "*", str(tmp_path / "backup"), "", "", "no"]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    with (
        patch.object(core.Config, "remote", return_value=""),
        patch.object(core, "plan_sources", return_value=[]),
    ):
        assert main(["--config", str(config), "setup", "--terminal", "--language", "en"]) == 0
    assert core.Config.load(config).snapshot_prefix == ""
    assert "Your TrueNAS username and NAS address" in capsys.readouterr().out


def test_existing_target_is_rejected_without_modification(tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    item = target / "keep"
    item.write_text("original")
    with pytest.raises(ValueError, match="neu oder leer"):
        Guide("de").destination(str(target))
    assert item.read_text() == "original"
