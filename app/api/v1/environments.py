from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.core.dependencies import CurrentUser, DbSession
from app.core.security import decrypt_value, encrypt_value, mask_secret
from app.models.environment import Environment
from app.schemas.environment import EnvironmentCreate, EnvironmentRead

router = APIRouter()


def _to_schema(environment: Environment) -> EnvironmentRead:
    masked = {}
    for key, value in environment.variables_encrypted.items():
        try:
            decrypted = decrypt_value(str(value))
            masked[key] = mask_secret(decrypted) or ""
        except Exception:
            masked[key] = mask_secret(str(value)) or ""
    return EnvironmentRead.model_validate({**environment.__dict__, "variables": masked})


def _encrypted_variables_for_update(environment: Environment, variables: dict[str, str]) -> dict[str, str]:
    encrypted: dict[str, str] = {}
    existing = environment.variables_encrypted or {}
    for key, value in variables.items():
        existing_value = existing.get(key)
        if existing_value is not None:
            try:
                if mask_secret(decrypt_value(str(existing_value))) == value:
                    encrypted[key] = str(existing_value)
                    continue
            except Exception:
                if mask_secret(str(existing_value)) == value:
                    encrypted[key] = str(existing_value)
                    continue
        encrypted[key] = encrypt_value(value)
    return encrypted


@router.get("", response_model=list[EnvironmentRead])
async def list_environments(db: DbSession, _: CurrentUser):
    result = await db.execute(select(Environment).order_by(Environment.created_at.desc()))
    return [_to_schema(item) for item in result.scalars()]


@router.post("", response_model=EnvironmentRead)
async def create_environment(payload: EnvironmentCreate, db: DbSession, _: CurrentUser):
    environment = Environment(
        name=payload.name,
        base_url=payload.base_url,
        variables_encrypted={key: encrypt_value(value) for key, value in payload.variables.items()},
        is_production=payload.is_production,
    )
    db.add(environment)
    await db.commit()
    await db.refresh(environment)
    return _to_schema(environment)


@router.put("/{environment_id}", response_model=EnvironmentRead)
async def update_environment(environment_id: str, payload: EnvironmentCreate, db: DbSession, _: CurrentUser):
    environment = await db.get(Environment, environment_id)
    if environment is None:
        raise HTTPException(status_code=404, detail="Environment not found")
    environment.name = payload.name
    environment.base_url = payload.base_url
    environment.variables_encrypted = _encrypted_variables_for_update(environment, payload.variables)
    environment.is_production = payload.is_production
    await db.commit()
    await db.refresh(environment)
    return _to_schema(environment)


@router.delete("/{environment_id}")
async def delete_environment(environment_id: str, db: DbSession, _: CurrentUser):
    environment = await db.get(Environment, environment_id)
    if environment is None:
        raise HTTPException(status_code=404, detail="Environment not found")
    await db.delete(environment)
    await db.commit()
    return {"message": "deleted"}
