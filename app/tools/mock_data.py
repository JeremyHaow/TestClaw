from __future__ import annotations

from copy import deepcopy
from typing import Any

from faker import Faker


DEFAULT_MOCK_LOCALE = "zh_CN"
DEFAULT_MOCK_SEED = 20260520
MAX_SCHEMA_DEPTH = 6
MAX_OBJECT_FIELDS = 20
MAX_ARRAY_ITEMS = 2


def generate_mock_json_body(
    schema: dict[str, Any] | None,
    *,
    document: dict[str, Any] | None = None,
    required_fields: list[str] | None = None,
    field_context: str = "",
    locale: str = DEFAULT_MOCK_LOCALE,
    seed: int = DEFAULT_MOCK_SEED,
) -> Any:
    """Generate a realistic JSON body from an OpenAPI/JSON schema."""
    if not schema:
        return None

    fake = Faker(locale)
    fake.seed_instance(seed)
    normalized_schema = _normalize_legacy_schema(_resolve_schema(schema, document))
    body = _generate_value(
        normalized_schema,
        fake,
        field_name=field_context,
        document=document,
        required_fields=required_fields or [],
        depth=0,
    )
    if body is None and required_fields:
        return {
            field: _generate_named_scalar(field, {"type": "string"}, fake)
            for field in required_fields
        }
    return body


def resolve_json_schema(
    schema: dict[str, Any] | None,
    document: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve local OpenAPI refs and normalize legacy shorthand schema shapes."""
    return _normalize_legacy_schema(_resolve_schema(schema, document))


def summarize_mock_body(body: Any) -> dict[str, Any]:
    """Return a safe, non-secret summary of generated request body shape."""
    if isinstance(body, dict):
        return {
            "shape": "object",
            "field_count": len(body),
            "fields": sorted(str(key) for key in body.keys())[:50],
        }
    if isinstance(body, list):
        return {"shape": "array", "item_count": len(body)}
    return {"shape": type(body).__name__ if body is not None else "null"}


def _resolve_ref(ref: str, document: dict[str, Any] | None) -> dict[str, Any]:
    if not ref.startswith("#/") or not isinstance(document, dict):
        return {}
    node: Any = document
    for part in ref.lstrip("#/").split("/"):
        if not isinstance(node, dict):
            return {}
        node = node.get(part, {})
    return deepcopy(node) if isinstance(node, dict) else {}


def _merge_object_schemas(schemas: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    for schema in schemas:
        if not isinstance(schema, dict):
            continue
        properties = schema.get("properties")
        if isinstance(properties, dict):
            merged["properties"].update(properties)
        required = schema.get("required")
        if isinstance(required, list):
            merged["required"].extend(field for field in required if field not in merged["required"])
        for key, value in schema.items():
            if key not in {"properties", "required", "type"}:
                merged.setdefault(key, value)
    if not merged["properties"]:
        merged.pop("properties", None)
    if not merged["required"]:
        merged.pop("required", None)
    return merged


def _resolve_schema(
    schema: dict[str, Any] | None,
    document: dict[str, Any] | None,
    depth: int = 0,
) -> dict[str, Any]:
    if depth > MAX_SCHEMA_DEPTH or not isinstance(schema, dict):
        return {}

    ref = schema.get("$ref")
    if isinstance(ref, str):
        resolved = _resolve_ref(ref, document)
        if resolved:
            return _resolve_schema(resolved, document, depth + 1)

    if "allOf" in schema and isinstance(schema["allOf"], list):
        parts = [_resolve_schema(part, document, depth + 1) for part in schema["allOf"]]
        overlay = {key: value for key, value in schema.items() if key != "allOf"}
        if overlay:
            parts.append(_resolve_schema(overlay, document, depth + 1))
        return _merge_object_schemas(parts)

    for choice_key in ("oneOf", "anyOf"):
        choices = schema.get(choice_key)
        if isinstance(choices, list) and choices:
            return _resolve_schema(choices[0], document, depth + 1)

    resolved = deepcopy(schema)
    properties = resolved.get("properties")
    if isinstance(properties, dict):
        resolved["properties"] = {
            name: _resolve_schema(prop, document, depth + 1) if isinstance(prop, dict) else prop
            for name, prop in properties.items()
        }
    items = resolved.get("items")
    if isinstance(items, dict):
        resolved["items"] = _resolve_schema(items, document, depth + 1)
    return resolved


def _normalize_legacy_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Support Swagger form-data shapes like {"name": "string"}."""
    if not isinstance(schema, dict):
        return {}
    if any(key in schema for key in ("type", "properties", "items", "oneOf", "anyOf", "allOf")):
        return schema
    if not schema:
        return schema
    properties: dict[str, Any] = {}
    for name, value in schema.items():
        if isinstance(value, str):
            properties[str(name)] = {"type": value}
        elif isinstance(value, dict):
            properties[str(name)] = value
    if not properties:
        return schema
    return {"type": "object", "properties": properties, "required": list(properties.keys())}


def _explicit_schema_value(schema: dict[str, Any]) -> Any:
    for key in ("example", "default"):
        if key in schema and schema[key] not in (None, ""):
            return deepcopy(schema[key])
    examples = schema.get("examples")
    if isinstance(examples, list) and examples:
        return deepcopy(examples[0])
    if isinstance(examples, dict) and examples:
        first = next(iter(examples.values()))
        if isinstance(first, dict) and "value" in first:
            return deepcopy(first["value"])
        return deepcopy(first)
    return None


def _schema_type(schema: dict[str, Any], field_name: str) -> str:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), None)
    if schema_type:
        return str(schema_type)
    if "properties" in schema or str(field_name).strip() == "":
        return "object"
    if "items" in schema:
        return "array"
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return type(enum[0]).__name__
    return "string"


def _generate_value(
    schema: dict[str, Any],
    fake: Faker,
    *,
    field_name: str = "",
    document: dict[str, Any] | None = None,
    required_fields: list[str] | None = None,
    depth: int = 0,
) -> Any:
    if depth > MAX_SCHEMA_DEPTH:
        return None
    if not isinstance(schema, dict):
        return None

    explicit = _explicit_schema_value(schema)
    if explicit is not None:
        return explicit

    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return deepcopy(enum[0])

    schema = _resolve_schema(schema, document, depth)
    schema_type = _schema_type(schema, field_name)

    if schema_type == "object" or "properties" in schema:
        return _generate_object(schema, fake, document, required_fields or [], depth)
    if schema_type == "array":
        return _generate_array(schema, fake, field_name, document, depth)
    if schema_type == "integer":
        return _generate_integer(field_name, schema)
    if schema_type == "number":
        return _generate_number(field_name, schema)
    if schema_type == "boolean":
        return True
    return _generate_named_scalar(field_name, schema, fake)


def _generate_object(
    schema: dict[str, Any],
    fake: Faker,
    document: dict[str, Any] | None,
    required_fields: list[str],
    depth: int,
) -> dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        additional = schema.get("additionalProperties")
        if isinstance(additional, dict):
            return {"key": _generate_value(additional, fake, document=document, depth=depth + 1)}
        return {}

    schema_required = [field for field in schema.get("required", []) if isinstance(field, str)]
    required = list(dict.fromkeys([*schema_required, *required_fields]))
    ordered_fields = [field for field in required if field in properties]
    ordered_fields.extend(field for field in properties if field not in ordered_fields)

    result: dict[str, Any] = {}
    for field in ordered_fields[:MAX_OBJECT_FIELDS]:
        prop = properties.get(field)
        if not isinstance(prop, dict) or prop.get("readOnly") is True:
            continue
        result[field] = _generate_value(
            prop,
            fake,
            field_name=field,
            document=document,
            required_fields=[],
            depth=depth + 1,
        )
    return result


def _generate_array(
    schema: dict[str, Any],
    fake: Faker,
    field_name: str,
    document: dict[str, Any] | None,
    depth: int,
) -> list[Any]:
    item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {"type": "string"}
    min_items = _bounded_int(schema.get("minItems"), default=1, lower=0, upper=MAX_ARRAY_ITEMS)
    item_count = max(1, min_items)
    item_count = min(item_count, MAX_ARRAY_ITEMS)
    return [
        _generate_value(
            item_schema,
            fake,
            field_name=_singularize(field_name),
            document=document,
            required_fields=[],
            depth=depth + 1,
        )
        for _ in range(item_count)
    ]


def _generate_integer(field_name: str, schema: dict[str, Any]) -> int:
    normalized = _normalize_name(field_name)
    if "age" in normalized or "年龄" in normalized:
        value = 30
    elif any(
        word in normalized
        for word in ("count", "total", "quantity", "qty", "num", "size", "数量", "总数")
    ):
        value = 2
    else:
        value = 1
    return _bounded_int(value, schema.get("minimum"), schema.get("maximum"))


def _generate_number(field_name: str, schema: dict[str, Any]) -> float:
    normalized = _normalize_name(field_name)
    if any(
        word in normalized
        for word in ("price", "amount", "money", "balance", "fee", "cost", "价格", "金额", "余额", "费用")
    ):
        value = 99.9
    elif any(word in normalized for word in ("rate", "ratio", "percent")):
        value = 0.8
    else:
        value = 1.0
    return float(_bounded_number(value, schema.get("minimum"), schema.get("maximum")))


def _generate_named_scalar(field_name: str, schema: dict[str, Any], fake: Faker) -> str:
    normalized = _normalize_name(field_name)
    fmt = str(schema.get("format") or "").lower()

    if fmt == "email" or "email" in normalized or "邮箱" in normalized or "邮件" in normalized:
        value = fake.email()
    elif fmt == "uuid" or "uuid" in normalized:
        value = str(fake.uuid4())
    elif fmt in {"uri", "url"} or any(word in normalized for word in ("url", "uri", "link")):
        value = fake.url()
    elif fmt == "date-time" or "datetime" in normalized or normalized.endswith("time"):
        value = _generate_datetime_string(fake)
    elif fmt == "date" or "date" in normalized or normalized.endswith("day"):
        value = fake.date_between(start_date="-30d", end_date="+30d").isoformat()
    elif any(word in normalized for word in ("phone", "mobile", "tel", "手机", "电话", "联系方式")):
        value = fake.phone_number()
    elif "password" in normalized or "passwd" in normalized or "密码" in normalized:
        value = "TestClaw@123456"
    elif any(word in normalized for word in ("token", "secret", "credential")):
        value = fake.sha256()[:32]
    elif normalized.endswith("id") or normalized in {"id", "uid"}:
        value = str(fake.uuid4())
    elif any(word in normalized for word in ("username", "account", "login", "账号", "账户", "用户名")):
        value = fake.user_name()
    elif "name" in normalized or "名称" in normalized or "姓名" in normalized:
        value = fake.name()
    elif any(word in normalized for word in ("title", "subject", "标题", "主题")):
        value = fake.sentence(nb_words=4).rstrip("。.")
    elif any(
        word in normalized
        for word in ("description", "remark", "comment", "content", "note", "描述", "备注", "内容")
    ):
        value = fake.text(max_nb_chars=80)
    elif "address" in normalized or "地址" in normalized:
        value = fake.address().replace("\n", " ")
    elif "city" in normalized or "城市" in normalized:
        value = fake.city()
    elif "province" in normalized or "state" in normalized or "省份" in normalized:
        value = fake.province()
    elif "country" in normalized or "国家" in normalized:
        value = "CN"
    elif "zip" in normalized or "postal" in normalized:
        value = fake.postcode()
    elif "status" in normalized or "状态" in normalized:
        value = "active"
    elif "type" in normalized or "category" in normalized or "类型" in normalized or "分类" in normalized:
        value = "default"
    elif "code" in normalized or "编码" in normalized or "代码" in normalized:
        value = fake.bothify(text="??####").upper()
    else:
        value = f"test_{normalized or 'value'}"

    return _fit_string(str(value), schema)


def _fit_string(value: str, schema: dict[str, Any]) -> str:
    min_length = _int_or_none(schema.get("minLength"))
    max_length = _int_or_none(schema.get("maxLength"))
    if min_length and len(value) < min_length:
        value = value + ("x" * (min_length - len(value)))
    if max_length and max_length > 0 and len(value) > max_length:
        value = value[:max_length]
    return value


def _generate_datetime_string(fake: Faker) -> str:
    return fake.date_time_between(start_date="-30d", end_date="+30d").strftime("%Y-%m-%d %H:%M:%S")


def _bounded_int(
    value: Any,
    minimum: Any = None,
    maximum: Any = None,
    *,
    default: int | None = None,
    lower: int | None = None,
    upper: int | None = None,
) -> int:
    parsed = _int_or_none(value)
    if parsed is None:
        parsed = default if default is not None else 1
    min_value = _int_or_none(minimum)
    max_value = _int_or_none(maximum)
    if lower is not None:
        min_value = lower if min_value is None else max(min_value, lower)
    if upper is not None:
        max_value = upper if max_value is None else min(max_value, upper)
    if min_value is not None:
        parsed = max(parsed, min_value)
    if max_value is not None:
        parsed = min(parsed, max_value)
    return parsed


def _bounded_number(value: float, minimum: Any = None, maximum: Any = None) -> float:
    min_value = _float_or_none(minimum)
    max_value = _float_or_none(maximum)
    if min_value is not None:
        value = max(value, min_value)
    if max_value is not None:
        value = min(value, max_value)
    return value


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_name(value: str) -> str:
    return "".join(ch.lower() for ch in str(value) if ch.isalnum())


def _singularize(value: str) -> str:
    text = str(value or "item")
    return text[:-1] if text.endswith("s") and len(text) > 1 else text
