"""Tests for authorize_via_local_server's InstalledAppFlow parameters.

We don't actually run the OAuth flow (would need a real browser); we only
assert that the flow is invoked with the right port/host/open_browser settings
so the SSH-tunnel-based headless workflow keeps working.
"""
from unittest.mock import MagicMock, patch

from daely_google_bridge.google_client import GoogleClient


@patch("daely_google_bridge.google_client.InstalledAppFlow")
def test_authorize_uses_fixed_port_8080(MockFlow, tmp_path, monkeypatch):
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")  # InstalledAppFlow is mocked, so content irrelevant
    flow_instance = MagicMock()
    MockFlow.from_client_secrets_file.return_value = flow_instance
    monkeypatch.delenv("BRIDGE_OAUTH_HOST", raising=False)

    GoogleClient.authorize_via_local_server(secrets)

    MockFlow.from_client_secrets_file.assert_called_once()
    flow_instance.run_local_server.assert_called_once()
    kwargs = flow_instance.run_local_server.call_args.kwargs
    assert kwargs["host"] == "localhost"
    assert kwargs["port"] == 8080
    assert kwargs["open_browser"] is False
    assert "SSH-Tunnel" in kwargs["authorization_prompt_message"]
    assert "{url}" in kwargs["authorization_prompt_message"]


@patch("daely_google_bridge.google_client.InstalledAppFlow")
def test_authorize_host_overridable_via_env(MockFlow, tmp_path, monkeypatch):
    """Docker requires bind to 0.0.0.0; we expose this via BRIDGE_OAUTH_HOST."""
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    monkeypatch.setenv("BRIDGE_OAUTH_HOST", "0.0.0.0")
    flow_instance = MagicMock()
    MockFlow.from_client_secrets_file.return_value = flow_instance

    GoogleClient.authorize_via_local_server(secrets)
    kwargs = flow_instance.run_local_server.call_args.kwargs
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 8080


@patch("daely_google_bridge.google_client.InstalledAppFlow")
def test_authorize_passes_scopes(MockFlow, tmp_path):
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    GoogleClient.authorize_via_local_server(
        secrets, scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    args, kwargs = MockFlow.from_client_secrets_file.call_args
    assert kwargs["scopes"] == ["https://www.googleapis.com/auth/calendar.readonly"]
