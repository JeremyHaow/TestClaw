from app.tools.doc_parser import parse_api_document_content


def test_parse_openapi_document():
    content = '{"openapi":"3.0.0","paths":{"/users":{"get":{"summary":"List users"}}}}'
    endpoints = parse_api_document_content(content, "openapi")
    assert len(endpoints) == 1
    assert endpoints[0]["path"] == "/users"
    assert endpoints[0]["method"] == "GET"
    assert endpoints[0]["summary"] == "List users"
    assert endpoints[0]["operationId"] == ""
    assert endpoints[0]["query_params"] == []
    assert endpoints[0]["response_status"] == "200"


def test_parse_postman_collection():
    content = (
        '{"info":{"name":"demo"},"item":[{"name":"Ping",'
        '"request":{"method":"GET","url":{"raw":"https://example.com/ping"}}}]}'
    )
    endpoints = parse_api_document_content(content, "postman")
    assert endpoints[0]["method"] == "GET"
    assert endpoints[0]["summary"] == "Ping"


def test_parse_openapi_request_body_ref_generates_mock_example():
    content = """
openapi: 3.0.0
components:
  schemas:
    CreateUserRequest:
      type: object
      required: [email, password]
      properties:
        email:
          type: string
          format: email
        password:
          type: string
          minLength: 8
paths:
  /users:
    post:
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateUserRequest'
      responses:
        '201':
          description: Created
"""

    endpoints = parse_api_document_content(content, "yaml")
    endpoint = endpoints[0]

    assert endpoint["request_body_schema"]["required"] == ["email", "password"]
    assert "@" in endpoint["example_request"]["email"]
    assert endpoint["example_request"]["password"] == "TestClaw@123456"
