from src.windows.apps import AppLauncher


def test_known_app_aliases() -> None:
    launcher = AppLauncher()

    try:
        assert (
            launcher.normalize("Chrome")
            == "chrome.exe"
        )

        assert (
            launcher.normalize("Calculator")
            == "calc.exe"
        )

        assert (
            launcher.normalize("VS Code")
            == "code.exe"
        )

    finally:
        launcher.close()


def test_custom_application_name() -> None:
    launcher = AppLauncher()

    try:
        assert (
            launcher.normalize(
                "MyApplication.exe"
            )
            == "MyApplication.exe"
        )

    finally:
        launcher.close()


def test_invalid_target() -> None:
    launcher = AppLauncher()

    try:
        try:
            launcher.open(
                "Calculator",
                target="invalid",  # type: ignore[arg-type]
            )
        except ValueError as exc:
            assert "target" in str(exc)
        else:
            raise AssertionError(
                "Expected ValueError."
            )

    finally:
        launcher.close()