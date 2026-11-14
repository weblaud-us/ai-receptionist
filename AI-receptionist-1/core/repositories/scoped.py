"""
ScopedDB — a database handle that adds `tenant_id` to every query, insert and update.

The pre-Phase-2 routers (hotel bookings/rooms, the admin API) were written against `get_db()`.
Instead of rewriting each query, they now take `db = scoped_db(tenant)`: the same Motor-style
calls, but every filter gets `tenant_id`, every inserted document gets it, and aggregations get a
leading `$match`. For the legacy .env hotel (no tenants yet) it returns the raw database, whose
documents carry no tenant_id.

New code uses `core.repositories.Repos`; this exists so old code cannot leak across tenants.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.tenancy.models import TenantConfig
from database.mongodb import get_db

# Collections that are platform-level, never scoped.
UNSCOPED = frozenset({"tenants", "users", "sessions", "password_resets", "invites"})


class ScopedCollection:
    def __init__(self, coll: Any, tenant_id: str) -> None:
        self._c = coll
        self._t = tenant_id

    @property
    def name(self) -> str:
        return self._c.name

    def _q(self, q: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {**(q or {}), "tenant_id": self._t}

    def _doc(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        doc["tenant_id"] = self._t            # mutate like Motor does (callers read _id back)
        return doc

    def _update(self, update: Any, upsert: bool) -> Any:
        if not isinstance(update, dict):
            return update
        update = dict(update)
        if "$set" in update:
            update["$set"] = {k: v for k, v in update["$set"].items() if k != "tenant_id"}
        if upsert:
            update["$setOnInsert"] = {**update.get("$setOnInsert", {}), "tenant_id": self._t}
        return update

    def find(self, q: Optional[Dict[str, Any]] = None, *args: Any, **kw: Any) -> Any:
        return self._c.find(self._q(q), *args, **kw)

    async def find_one(self, q: Optional[Dict[str, Any]] = None, *args: Any, **kw: Any) -> Any:
        return await self._c.find_one(self._q(q), *args, **kw)

    async def count_documents(self, q: Optional[Dict[str, Any]] = None, **kw: Any) -> int:
        return await self._c.count_documents(self._q(q), **kw)

    async def estimated_document_count(self) -> int:
        return await self._c.count_documents(self._q())

    async def distinct(self, key: str, q: Optional[Dict[str, Any]] = None, **kw: Any) -> List[Any]:
        return await self._c.distinct(key, self._q(q), **kw)

    def aggregate(self, pipeline: List[Dict[str, Any]], **kw: Any) -> Any:
        return self._c.aggregate([{"$match": {"tenant_id": self._t}}, *pipeline], **kw)

    async def insert_one(self, doc: Dict[str, Any], **kw: Any) -> Any:
        return await self._c.insert_one(self._doc(doc), **kw)

    async def insert_many(self, docs: List[Dict[str, Any]], **kw: Any) -> Any:
        return await self._c.insert_many([self._doc(d) for d in docs], **kw)

    async def update_one(self, q: Dict[str, Any], update: Any, upsert: bool = False, **kw: Any) -> Any:
        return await self._c.update_one(self._q(q), self._update(update, upsert), upsert=upsert, **kw)

    async def update_many(self, q: Dict[str, Any], update: Any, upsert: bool = False, **kw: Any) -> Any:
        return await self._c.update_many(self._q(q), self._update(update, upsert), upsert=upsert, **kw)

    async def delete_one(self, q: Dict[str, Any], **kw: Any) -> Any:
        return await self._c.delete_one(self._q(q), **kw)

    async def delete_many(self, q: Dict[str, Any], **kw: Any) -> Any:
        return await self._c.delete_many(self._q(q), **kw)

    async def find_one_and_update(self, q: Dict[str, Any], update: Any, *args: Any, upsert: bool = False, **kw: Any) -> Any:
        return await self._c.find_one_and_update(self._q(q), self._update(update, upsert), *args, upsert=upsert, **kw)


class ScopedDB:
    def __init__(self, db: Any, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id required")
        self._db = db
        self.tenant_id = tenant_id

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __getitem__(self, name: str) -> Any:
        if name in UNSCOPED:
            return self._db[name]
        return ScopedCollection(self._db[name], self.tenant_id)

    async def command(self, *args: Any, **kw: Any) -> Any:
        return await self._db.command(*args, **kw)


def scoped_db(tenant: TenantConfig) -> Any:
    """Tenant-scoped handle; the raw database for the pre-migration legacy hotel."""
    db = get_db()
    return db if tenant.is_legacy else ScopedDB(db, tenant.id)
