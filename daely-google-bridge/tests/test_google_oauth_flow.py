"""Tests for authorize_via_local_server's InstalledAppFlow parameters.

We don't actually run the OAuth flow (would need a real browser); we only
assert that the flow is invoked with the right port/host/open_browser settings
so the SSH-tunnel-based headless workflow keeps working.
"""
from unittest.mock import MagicMock, patch

from daely_google_bridge.google_client import GoogleClient


@patch("daely_google_bridge.google_client.InstalledAppFlow")
def test_authorize_default_port_is_8080(MockFlow, tmp_path, monkeypatch):
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")  # InstalledAppFlow is mocked, so content irrelevant
    flow_instance = MagicMock()
    MockFlow.from_client_secrets_file.return_value = flow_instance
    monkeypatch.delenv("BRIDGE_OAUTH_HOST", raising=False)

    GoogleClient.authorize_via_local_server(secrets)

    MockFlow.from_client_secrets_file.assert_called_once()
    flow_instance.run_local_server.assert_called_once()
    kwargs = flow_instance.run_local_server.call_args.kwargs
    # redirect_uri host: always localhost (Google policy)
    assert kwargs["host"] == "localhost"
    # bind_addr: None when BRIDGE_OAUTH_HOST unset → google-auth falls back to host
    assert kwargs["bind_addr"] is None
    assert kwargs["port"] == 8080
    assert kwargs["open_browser"] is False
    assert "SSH-Tunnel" in kwargs["authorization_prompt_message"]
    assert "ssh -L 8080:localhost:8080" in kwargs["authorization_prompt_message"]
    assert "{url}" in kwargs["authorization_prompt_message"]


@patch("daely_google_bridge.google_client.InstalledAppFlow")
def test_authorize_in_docker_separates_bind_addr_from_redirect_host(
    MockFlow, tmp_path, monkeypatch,
):
    """In Docker, BRIDGE_OAUTH_HOST=0.0.0.0 binds the listener on all interfaces,
    but the redirect_uri host stays localhost so Google doesn't reject the URI."""
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    monkeypatch.setenv("BRIDGE_OAUTH_HOST", "0.0.0.0")
    flow_instance = MagicMock()
    MockFlow.from_client_secrets_file.return_value = flow_instance

    GoogleClient.authorize_via_local_server(secrets)

    kwargs = flow_instance.run_local_server.call_args.kwargs
    assert kwargs["host"] == "localhost"      # for the redirect_uri
    assert kwargs["bind_addr"] == "0.0.0.0"   # for the actual TCP bind


@patch("daely_google_bridge.google_client.InstalledAppFlow")
def test_authorize_custom_port_is_forwarded(MockFlow, tmp_path, monkeypatch):
    """Caller can override the default 8080 via the `port` kwarg."""
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    monkeypatch.delenv("BRIDGE_OAUTH_HOST", raising=False)
    flow_instance = MagicMock()
    MockFlow.from_client_secrets_file.return_value = flow_instance

    GoogleClient.authorize_via_local_server(secrets, port=8765)

    kwargs = flow_instance.run_local_server.call_args.kwargs
    assert kwargs["port"] == 8765
    # host stays localhost regardless of port
    assert kwargs["host"] == "localhost"
    # Prompt text references the same custom port (so SSH-tunnel hint stays accurate)
    assert "ssh -L 8765:localhost:8765" in kwargs["authorization_prompt_message"]
    assert "ssh -L 8080:localhost:8080" not in kwargs["authorization_prompt_message"]
    # `{url}` placeholder is preserved (google-auth substitutes it)
    assert "{url}" in kwargs["authorization_prompt_message"]


@patch("daely_google_bridge.google_client.InstalledAppFlow")
def test_authorize_host_overridable_via_env(MockFlow, tmp_path, monkeypatch):
    """BRIDGE_OAUTH_HOST controls the LISTENER bind, not the redirect_uri host.
    Google requires localhost in the redirect_uri; we always set host=localhost."""
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    monkeypatch.setenv("BRIDGE_OAUTH_HOST", "0.0.0.0")
    flow_instance = MagicMock()
    MockFlow.from_client_secrets_file.return_value = flow_instance

    GoogleClient.authorize_via_local_server(secrets)
    kwargs = flow_instance.run_local_server.call_args.kwargs
    assert kwargs["host"] == "localhost"       # for the redirect_uri (Google policy)
    assert kwargs["bind_addr"] == "0.0.0.0"    # for the actual listener
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
