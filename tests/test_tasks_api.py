from fastapi.testclient import TestClient

from app.main import app


def _token(client: TestClient) -> str:
    login = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'testclaw123'})
    assert login.status_code == 200
    return login.json()['access_token']


def test_create_and_list_tasks():
    with TestClient(app) as client:
        token = _token(client)
        headers = {'Authorization': f'Bearer {token}'}
        created = client.post(
            '/api/v1/tasks',
            json={'objective': '检查首页', 'target_url': 'http://example.com', 'test_type': 'ui'},
            headers=headers,
        )
        assert created.status_code == 200
        task_id = created.json()['id']

        listed = client.get('/api/v1/tasks', headers=headers)
        assert listed.status_code == 200
        assert any(item['id'] == task_id for item in listed.json())
