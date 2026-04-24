from app.tools.doc_parser import parse_api_document_content


def test_parse_openapi_document():
    content = '{"openapi":"3.0.0","paths":{"/users":{"get":{"summary":"List users"}}}}'
    endpoints = parse_api_document_content(content, "openapi")
    assert endpoints == [
        {"path": "/users", "method": "GET", "summary": "List users", "operationId": None}
    ]


def test_parse_postman_collection():
    content = '{"info":{"name":"demo"},"item":[{"name":"Ping","request":{"method":"GET","url":{"raw":"https://example.com/ping"}}}]}'
    endpoints = parse_api_document_content(content, "postman")
    assert endpoints[0]["method"] == "GET"
    assert endpoints[0]["summary"] == "Ping"
