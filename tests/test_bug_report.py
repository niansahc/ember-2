"""
tests/test_bug_report.py

Tests for the POST /v1/bug-report endpoint. All GitHub API calls are
mocked — no real issues are created, no real tokens are used.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    """Create a test client with auth disabled."""
    with patch("src.core.config.get_private_vault_path", return_value=tmp_path), \
         patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        yield TestClient(app)


class TestBugReport:
    """POST /v1/bug-report"""

    def test_creates_issue_on_success(self, client):
        """Happy path: token configured, GitHub returns 201."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "html_url": "https://github.com/niansahc/ember-2/issues/42",
        }

        with patch("keyring.get_password", return_value="ghp_fake_token"), \
             patch("httpx.post", return_value=mock_response):
            resp = client.post("/v1/bug-report", json={
                "title": "Test bug",
                "body": "Something broke",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert "issues/42" in data["url"]

    def test_returns_503_when_no_token(self, client):
        """No GitHub token in keyring → 503."""
        with patch("keyring.get_password", return_value=None):
            resp = client.post("/v1/bug-report", json={
                "title": "Test bug",
                "body": "No token",
            })

        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"]

    def test_returns_400_on_empty_title(self, client):
        """Empty title → 400."""
        resp = client.post("/v1/bug-report", json={
            "title": "   ",
            "body": "Missing title",
        })
        assert resp.status_code == 400
        assert "Title" in resp.json()["detail"]

    def test_forwards_github_error(self, client):
        """GitHub returns non-201 → forwarded as error."""
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = "Validation Failed"

        with patch("keyring.get_password", return_value="ghp_fake_token"), \
             patch("httpx.post", return_value=mock_response):
            resp = client.post("/v1/bug-report", json={
                "title": "Test bug",
                "body": "Will fail",
            })

        assert resp.status_code == 422

    def test_sends_correct_payload_to_github(self, client):
        """Verify the GitHub API call has the right structure."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"html_url": "https://example.com"}

        with patch("keyring.get_password", return_value="ghp_fake_token"), \
             patch("httpx.post", return_value=mock_response) as mock_post:
            client.post("/v1/bug-report", json={
                "title": "  Bug title  ",
                "body": "  Bug body  ",
            })

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        # Check URL
        assert "niansahc/ember-2/issues" in call_kwargs.args[0]
        # Check payload is trimmed
        assert call_kwargs.kwargs["json"]["title"] == "Bug title"
        assert call_kwargs.kwargs["json"]["body"] == "Bug body"
        # Check auth header
        assert "Bearer ghp_fake_token" in call_kwargs.kwargs["headers"]["Authorization"]
