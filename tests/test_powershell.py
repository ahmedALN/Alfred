from src.windows.powershell import PowerShellRunner


def test_powershell_echo() -> None:
    runner = PowerShellRunner()

    result = runner.run('Write-Output "ALFRED_TEST"')

    assert result.success
    assert "ALFRED_TEST" in result.stdout


def test_powershell_failure() -> None:
    runner = PowerShellRunner()

    result = runner.run(
        'Write-Error "intentional failure"'
    )

    assert not result.success
    assert result.return_code != 0
    assert "intentional failure" in result.stderr
