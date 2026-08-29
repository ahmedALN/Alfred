from src.windows.apps import AppLauncher


def _launcher(start_apps=None):
    lz = AppLauncher.__new__(AppLauncher)
    lz._start_apps_cache = start_apps or []
    lz._bridge = None
    lz._bridge_tried = True
    lz.alfred_desktop = 2
    lz.user_desktop = 1
    return lz


STORE = [
    {"name": "Spotify", "appid": "Spotify.exe"},
    {"name": "Visual Studio Code", "appid": "MSVSCode!VSCode"},
    {"name": "Google Chrome", "appid": "Chrome.appref"},
    {"name": "Discord", "appid": "com.squirrel.Discord.Discord"},
    {"name": "WhatsApp", "appid": "5319275A.WhatsAppDesktop_..."},
]


def test_resolves_store_app_by_loose_name():
    lz = _launcher(STORE)
    spec = lz.resolve("spotify")
    assert spec.kind == "appsfolder"
    assert spec.value == "Spotify.exe"
    assert spec.display == "Spotify"


def test_resolves_multiword_via_token_overlap():
    lz = _launcher(STORE)
    spec = lz.resolve("code editor")  # not an alias
    assert spec.kind == "appsfolder"
    assert spec.display == "Visual Studio Code"


def test_alias_takes_priority_for_system_tools():
    lz = _launcher(STORE)
    spec = lz.resolve("notepad")
    assert spec.kind == "exe"
    assert spec.value.lower().endswith("notepad.exe")


def test_uri_scheme_passthrough():
    lz = _launcher(STORE)
    assert lz.resolve("ms-settings:display").kind == "uri"
    assert lz.resolve("https://example.com").kind == "uri"
    assert lz.resolve("settings").kind == "uri"  # alias -> ms-settings:


def test_unknown_app_returns_none():
    lz = _launcher(STORE)
    assert lz.resolve("zxqw nonexistent gibberish") is None


def test_open_unknown_reports_not_found_with_hints():
    lz = _launcher(STORE)
    result = lz.open("spot xyz", target="current")
    assert result.status == "not_found"
    assert result.launched is False
    assert "Spotify" in (result.note or "")


def test_open_rejects_bad_target():
    lz = _launcher(STORE)
    try:
        lz.open("Spotify", target="nope")
    except ValueError as e:
        assert "target" in str(e)
    else:
        raise AssertionError("expected ValueError")
