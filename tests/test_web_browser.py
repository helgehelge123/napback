"""Real Chromium interaction tests; install playwright + Chromium to run."""

import threading
from unittest.mock import patch

import pytest
from web_fixture import fake_remote

from napback import core
from napback.webapp import Application, Server

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def browser_page(tmp_path, monkeypatch):
    monkeypatch.setattr(core.Config, "remote", fake_remote)
    app = Application(tmp_path / "config.json")
    server = Server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1080})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(server.origin + "/#" + server.token)
        yield page, app, errors
        browser.close()
    server.shutdown()
    server.server_close()
    thread.join()


def connect(page):
    page.locator("#address").fill("nas")
    page.locator("#username").fill("backup")
    page.locator("#connect-button").click()
    page.locator("#step-1").wait_for(state="visible")


def test_browser_snapshot_selection_review_save_and_reload(browser_page):
    page, app, errors = browser_page
    connect(page)
    page.get_by_role("checkbox", name="Vault sichern", exact=True).check()
    page.locator("#selection-report").get_by_text("Kein passender Snapshot", exact=False).wait_for()
    page.get_by_role("button", name="Snapshots von Vault anzeigen", exact=True).click()
    page.locator("#snapshot-list").get_by_text("auto-2", exact=True).wait_for()
    page.locator("#close-snapshots").click()
    page.get_by_role("button", name="Diese Bereiche ohne passenden Snapshot abwählen").click()
    page.locator("#selection-report").get_by_text("Wird gesichert: auto-2", exact=False).wait_for()
    page.locator("#to-storage").click()
    page.locator("#step-2").wait_for(state="visible")
    page.locator("#target").fill(str(app.config_path.parent / "browser-backup"))
    page.locator("#automatic").uncheck()
    page.locator("#minutes").fill("5")
    page.locator("#review-button").click()
    page.locator("#step-3").wait_for(state="visible")
    assert not app.config_path.exists()
    assert page.locator("#review-content").get_by_text("Vault/missing", exact=True).count() == 1
    page.screenshot(path="/tmp/napback-web-review.png", full_page=True)
    with patch("napback.webapp.subprocess.run"):
        page.locator("#save-button").click()
        page.locator("#dashboard").wait_for(state="visible")
        page.locator("#dashboard-content h2").get_by_text("Deine gespeicherte Auswahl").wait_for()
    config = core.Config.load(app.config_path)
    assert config.check_interval_minutes == 5
    assert "Vault/missing" in config.sources[0]["exclude"]
    page.reload()
    page.locator("#dashboard").wait_for(state="visible")
    assert not errors


def test_browser_empty_snapshot_failure_preserves_form_and_config(browser_page):
    page, app, errors = browser_page
    connect(page)
    page.get_by_role("checkbox", name="Vault/missing sichern", exact=True).check()
    page.locator("#selection-report").get_by_text("Kein passender Snapshot", exact=False).wait_for()
    page.locator("#to-storage").click()
    page.locator("#target").fill(str(app.config_path.parent / "never-created"))
    page.locator("#review-button").click()
    page.locator("#step-1").wait_for(state="visible")
    page.locator("#notice").get_by_text(
        "Die Auswahl ist noch nicht vollständig sicherbar.", exact=False
    ).wait_for()
    assert not app.config_path.exists()
    assert not (app.config_path.parent / "never-created").exists()
    assert page.get_by_role("checkbox", name="Vault/missing sichern", exact=True).is_checked()
    assert not errors


def test_browser_folder_picker_and_mobile_layout(browser_page):
    page, app, errors = browser_page
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    connect(page)
    page.get_by_role("checkbox", name="Vault/photos sichern", exact=True).check()
    page.locator("#selection-report").get_by_text("Wird gesichert:", exact=False).wait_for()
    page.locator("#to-storage").click()
    page.locator("#choose-folder").click()
    page.locator("#folder-dialog").wait_for(state="visible")
    page.locator("#folder-name").fill("My Backup")
    page.locator("#folder-use").click()
    assert page.locator("#target").input_value().endswith("/My Backup")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors
