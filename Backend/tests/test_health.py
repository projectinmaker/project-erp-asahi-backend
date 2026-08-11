"""
Asahi ERP - Health Endpoint Tests
Tests dasar untuk memverifikasi setup berjalan dengan benar
"""


class TestHealthEndpoint:
    """Tests untuk /health endpoint"""

    def test_health_returns_ok(self, client):
        """Health endpoint harus return 200"""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_has_required_fields(self, client):
        """Health response harus punya field yang dibutuhkan"""
        response = client.get("/health")
        data = response.json()

        assert "status" in data
        assert "app" in data
        assert "version" in data
        assert "environment" in data
        assert "database" in data

    def test_health_app_name(self, client):
        """Health response harus punya nama app yang benar"""
        response = client.get("/health")
        data = response.json()

        assert data["app"] == "Asahi ERP API"
        assert data["version"] == "0.1.0"


class TestRootEndpoint:
    """Tests untuk / endpoint"""

    def test_root_returns_ok(self, client):
        """Root endpoint harus return 200"""
        response = client.get("/")
        assert response.status_code == 200

    def test_root_has_name(self, client):
        """Root response harus punya nama app"""
        response = client.get("/")
        data = response.json()

        assert data["name"] == "Asahi ERP API"


class TestApiV1Ping:
    """Tests untuk /api/v1/ping endpoint"""

    def test_ping_returns_pong(self, client):
        """Ping endpoint harus return pong"""
        response = client.get("/api/v1/ping")
        assert response.status_code == 200

        data = response.json()
        assert data["message"] == "pong"
        assert data["version"] == "v1"
