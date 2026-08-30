from android_runner.adb import ADBClient, parse_devices


def test_parse_devices_only_returns_ready_devices():
    output = """List of devices attached
serial1    device product:foo model:Bar device:bar
serial2    unauthorized usb:1-1
serial3    offline
"""
    assert parse_devices(output) == [{"serial": "serial1", "state": "device", "model": "Bar"}]


def test_adb_client_builds_serial_command():
    client = ADBClient("adb.exe", "SERIAL")
    assert client.command("shell", "getprop") == ["adb.exe", "-s", "SERIAL", "shell", "getprop"]
