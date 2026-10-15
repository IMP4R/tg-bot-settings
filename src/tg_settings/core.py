from __future__ import annotations

import copy
import inspect
import json
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .codecs import Codec, ValidationError


class ConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class Setting:
    key: str
    codec: Codec
    default: Any
    title: str = ''
    description: str = ''
    category: str = 'general'
    secret: bool = False
    readonly: bool = False
    validate: Callable[[Any], bool] | None = None

    def checked(self, value):
        value = self.codec.validate(value)
        if self.validate and not self.validate(value):
            raise ValidationError('Custom validation failed')
        return copy.deepcopy(value)


class Schema:
    def __init__(self, *settings: Setting):
        self._fields = {}
        for setting in settings:
            self.register(setting)

    def register(self, setting: Setting):
        if not setting.key or setting.key in self._fields:
            raise ValidationError('Empty or duplicate setting key')
        setting.checked(setting.default)
        self._fields[setting.key] = copy.deepcopy(setting)
        return self

    def get(self, key):
        return self._fields[key]

    def fields(self, category=None):
        return [s for s in self._fields.values() if category is None or s.category == category]

    def categories(self):
        return list(dict.fromkeys(s.category for s in self._fields.values()))


class Store(Protocol):
    def snapshot(self, scope: str) -> tuple[int, dict]: ...
    def patch(self, scope: str, changes: dict, deletes: tuple, expected_version: int | None) -> int: ...


class MemoryStore:
    def __init__(self):
        self._data = {}
        self._lock = threading.RLock()

    def snapshot(self, scope):
        with self._lock:
            version, data = self._data.get(scope, (0, {}))
            return version, copy.deepcopy(data)

    def patch(self, scope, changes, deletes=(), expected_version=None):
        with self._lock:
            version, data = self.snapshot(scope)
            if expected_version is not None and version != expected_version:
                raise ConflictError('Settings changed; reopen editor')
            for key in deletes:
                data.pop(key, None)
            data.update(copy.deepcopy(changes))
            self._data[scope] = version + 1, data
            return version + 1


class SQLiteStore:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS settings_scopes (scope TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL)')
        self.db.commit()

    def snapshot(self, scope):
        row = self.db.execute('SELECT version,data FROM settings_scopes WHERE scope=?', (scope,)).fetchone()
        return (row[0], json.loads(row[1])) if row else (0, {})

    def patch(self, scope, changes, deletes=(), expected_version=None):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            version, data = self.snapshot(scope)
            if expected_version is not None and version != expected_version:
                raise ConflictError('Settings changed; reopen editor')
            for key in deletes:
                data.pop(key, None)
            data.update(changes)
            self.db.execute('INSERT OR REPLACE INTO settings_scopes VALUES (?,?,?)', (scope, version + 1, json.dumps(data, ensure_ascii=False, allow_nan=False)))
            self.db.commit()
            return version + 1
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()


@dataclass(frozen=True)
class Change:
    key: str
    old: Any
    new: Any
    scope: str
    version: int


class HookError(RuntimeError):
    """Persistence succeeded. Never retry the change because a notification failed."""
    def __init__(self, version, failures):
        self.version, self.failures = version, failures
        super().__init__('Settings committed, but change notification failed')


class Settings:
    def __init__(self, schema: Schema, store: Store | None = None, *, scope='global', cross_validate=None):
        self.schema, self.store, self.scope = schema, store or MemoryStore(), scope
        self.cross_validate = cross_validate
        self._hooks = []

    def snapshot(self, *, redact=False):
        version, stored = self.store.snapshot(self.scope)
        values = {}
        for s in self.schema.fields():
            value = s.checked(s.codec.decode(stored[s.key])) if s.key in stored else s.checked(s.default)
            values[s.key] = '••••' if redact and s.secret else value
        return version, values

    def get(self, key):
        self.schema.get(key)
        return self.snapshot()[1][key]

    def on_change(self, hook):
        self._hooks.append(hook)
        def unsubscribe():
            self._hooks.remove(hook)
        return unsubscribe

    def _prepared(self, changes, resets=()):
        version, old = self.snapshot()
        new = copy.deepcopy(old)
        encoded = {}
        for key, value in changes.items():
            s = self.schema.get(key)
            if s.readonly:
                raise PermissionError('Setting is read-only')
            new[key] = s.checked(value)
            encoded[key] = s.codec.encode(new[key])
        for key in resets:
            s = self.schema.get(key)
            if s.readonly:
                raise PermissionError('Setting is read-only')
            new[key] = s.checked(s.default)
        if self.cross_validate and not self.cross_validate(new):
            raise ValidationError('Cross-field validation failed')
        return version, old, new, encoded

    def preview(self, changes):
        version, _, new, _ = self._prepared(changes)
        return version, new

    async def patch(self, changes, *, expected_version=None, resets=()):
        version, old, new, encoded = self._prepared(changes, resets)
        # Always CAS the snapshot used for cross-field validation.
        if expected_version is not None and expected_version != version:
            raise ConflictError('Settings changed; reopen editor')
        committed = self.store.patch(self.scope, encoded, tuple(resets), version)
        failures = []
        for key in dict.fromkeys([*changes, *resets]):
            s = self.schema.get(key)
            event = Change(key, None if s.secret else old[key], None if s.secret else new[key], self.scope, committed)
            for hook in tuple(self._hooks):
                try:
                    result = hook(event)
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    failures.append(exc)
        if failures:
            raise HookError(committed, failures)
        return committed

    async def set(self, key, value, *, expected_version=None):
        return await self.patch({key: value}, expected_version=expected_version)

    async def reset(self, key, *, expected_version=None):
        return await self.patch({}, resets=(key,), expected_version=expected_version)
