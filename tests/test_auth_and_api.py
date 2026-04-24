from fastapi.testclient import TestClient

from app.main import app


def test_login_and_me_flow():
    with TestClient(app) as client:
        login = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'testclaw123'})
        assert login.status_code == 200
        token = login.json()['access_token']

        me = client.get('/api/v1/auth/me', headers={'Authorization': f'Bearer {token}'})
        assert me.status_code == 200
        assert me.json()['username'] == 'admin'


def test_dashboard_requires_auth():
    with TestClient(app) as client:
        response = client.get('/api/v1/dashboard/summary')
        assert response.status_code == 401
