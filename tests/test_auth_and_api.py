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


def test_provider_update_can_preserve_existing_api_key():
    with TestClient(app) as client:
        login = client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'testclaw123'})
        token = login.json()['access_token']
        headers = {'Authorization': f'Bearer {token}'}

        created = client.post(
            '/api/v1/providers',
            json={
                'name': 'Editable provider',
                'type': 'openai',
                'api_key': 'sk-original-secret',
                'model_name': 'gpt-test',
                'base_url': 'https://api.example.test/v1',
                'max_tokens': 1024,
                'temperature': 0.1,
            },
            headers=headers,
        )
        assert created.status_code == 200
        provider = created.json()

        updated = client.put(
            f'/api/v1/providers/{provider["id"]}',
            json={
                'name': 'Edited provider',
                'type': 'openai',
                'model_name': 'gpt-edited',
                'base_url': 'https://api.example.test/v2',
                'is_default_coder': True,
                'is_default_vision': False,
                'is_default_planner': True,
                'max_tokens': 2048,
                'temperature': 0.3,
                'system_prompt': 'Use concise test output.',
                'agent_type': 'planner',
            },
            headers=headers,
        )

        assert updated.status_code == 200
        body = updated.json()
        assert body['name'] == 'Edited provider'
        assert body['model_name'] == 'gpt-edited'
        assert body['api_key_masked'] == provider['api_key_masked']

        deleted = client.delete(f'/api/v1/providers/{provider["id"]}', headers=headers)
        assert deleted.status_code == 200
