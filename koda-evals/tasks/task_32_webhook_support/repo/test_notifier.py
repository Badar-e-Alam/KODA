import json
from unittest.mock import patch, MagicMock
from notifier import Notifier

def test_console_only():
    n = Notifier()
    # Just shouldn't crash
    n.notify("hello")

def test_webhook_success():
    n = Notifier()
    n.register_webhook("http://example.com/hook")

    with patch("notifier.urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value = mock_response
        n.notify("hello")
        # Verify it was called
        assert mock_urlopen.called
