from __future__ import annotations

import inspect
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from tg_templates import Button, Media, Template, UI

from .codecs import BoolCodec, ChoiceCodec, MediaCodec
from .core import Settings


@dataclass
class Session:
    actor: int
    expires: float
    key: str | None = None
    version: int | None = None
    pending: Any = None
    has_pending: bool = False
    reset: bool = False
    actions: dict = field(default_factory=dict)
    awaiting: bool = False


class SettingsUI:
    """Framework-independent admin controller. Host supplies actor and trusted auth."""
    def __init__(self, settings: Settings, *, authorize, ui: UI | None = None, ttl=900, page_size=6, templates=None, clock=time.monotonic):
        if ttl <= 0 or page_size < 1:
            raise ValueError('TTL and page size must be positive')
        self.settings, self.authorize, self.ui = settings, authorize, ui
        self.ttl, self.page_size, self.clock = ttl, page_size, clock
        self.sessions = {}
        self.templates = {
            'categories': '<b>Настройки</b>\nВыберите категорию',
            'list': '<b>{category}</b>\nСтраница {page}',
            'view': '<b>{title}</b>\n{description}\n\nЗначение: <code>{value}</code>',
            'input': '<b>{title}</b>\nВведите новое значение. Для media отправьте kind/source в JSON или Media через адаптер.',
            'preview': '<b>{title}</b>\nНовое значение: <code>{value}</code>\nПодтвердить изменение?',
        }
        self.templates.update(templates or {})

    async def _authorized(self, actor):
        result = self.authorize(actor)
        if inspect.isawaitable(result):
            result = await result
        if not result:
            raise PermissionError('Settings access denied')

    def _expire(self):
        now = self.clock()
        for token in tuple(self.sessions):
            if self.sessions[token].expires <= now:
                del self.sessions[token]

    def _button(self, session, text, action, *, style=None):
        action_token = secrets.token_urlsafe(9)
        session.actions[action_token] = action
        return Button(text, callback_data=f'set:{action_token}', style=style)

    def _page(self, session, kind, variables=None, buttons=(), media=None):
        return Template(text=self.templates[kind], buttons=tuple(tuple(row) for row in buttons), media=media), variables or {}

    def _categories(self, session):
        session.key, session.awaiting, session.has_pending = None, False, False
        buttons = [[self._button(session, category, ('category', category, 0))] for category in self.settings.schema.categories()]
        return self._page(session, 'categories', buttons=buttons)

    def _listing(self, session, category, page):
        fields = self.settings.schema.fields(category)
        pages = max(1, (len(fields) + self.page_size - 1) // self.page_size)
        page = max(0, min(page, pages - 1))
        buttons = [[self._button(session, s.title or s.key, ('view', s.key))] for s in fields[page*self.page_size:(page+1)*self.page_size]]
        nav = []
        if page:
            nav.append(self._button(session, 'Назад', ('category', category, page - 1)))
        if page + 1 < pages:
            nav.append(self._button(session, 'Далее', ('category', category, page + 1)))
        if nav:
            buttons.append(nav)
        buttons.append([self._button(session, 'Категории', ('categories',))])
        session.awaiting = False
        return self._page(session, 'list', {'category': category, 'page': page + 1}, buttons)

    def _display(self, setting, value):
        if setting.secret:
            return '••••'
        encoded = setting.codec.encode(value)
        if isinstance(encoded, (dict, list)):
            return json.dumps(encoded, ensure_ascii=False)
        return str(encoded)

    def _view(self, session, key):
        setting = self.settings.schema.get(key)
        session.version, values = self.settings.snapshot()
        session.key, session.awaiting, session.has_pending = key, False, False
        session.reset = False
        value = values[key]
        buttons = []
        if not setting.readonly:
            if isinstance(setting.codec, BoolCodec):
                buttons.append([self._button(session, 'Выключить' if value else 'Включить', ('propose', not value))])
            elif isinstance(setting.codec, ChoiceCodec):
                # Paginated choice screen avoids unbounded keyboards.
                buttons.append([self._button(session, 'Выбрать', ('choices', 0))])
            else:
                buttons.append([self._button(session, 'Изменить', ('input',))])
            buttons.append([self._button(session, 'Сбросить', ('reset',), style='danger')])
        buttons.append([self._button(session, 'Назад', ('category', setting.category, 0))])
        media = value if isinstance(value, Media) and not setting.secret else None
        display = self._display(setting, value)
        # Large JSON gets a summary; full value remains accessible through typed API.
        if len(display) > 600:
            display = display[:600] + '…'
        return self._page(session, 'view', {'title': setting.title or key, 'description': setting.description, 'value': display}, buttons, media)

    def _choices(self, session, page):
        setting = self.settings.schema.get(session.key)
        options = setting.codec.options
        pages = max(1, (len(options)+self.page_size-1)//self.page_size)
        page = max(0, min(page, pages-1))
        buttons = [[self._button(session, str(value), ('propose', value))] for value in options[page*self.page_size:(page+1)*self.page_size]]
        if page:
            buttons.append([self._button(session, 'Назад', ('choices', page-1))])
        if page+1 < pages:
            buttons.append([self._button(session, 'Далее', ('choices', page+1))])
        buttons.append([self._button(session, 'Отмена', ('view', session.key))])
        return self._page(session, 'list', {'category': setting.title or setting.key, 'page': page+1}, buttons)

    def _preview(self, session, value, *, reset=False):
        setting = self.settings.schema.get(session.key)
        value = setting.checked(value)
        self.settings.preview({session.key: value})
        session.pending, session.has_pending, session.reset, session.awaiting = value, True, reset, False
        buttons = [[self._button(session, 'Сохранить', ('save',), style='success')], [self._button(session, 'Отмена', ('view', session.key))]]
        media = value if isinstance(value, Media) and not setting.secret else None
        display = self._display(setting, value)
        if len(display) > 600:
            display = display[:600] + '…'
        return self._page(session, 'preview', {'title': setting.title or setting.key, 'value': display}, buttons, media)

    async def open(self, actor):
        await self._authorized(actor)
        self._expire()
        token = secrets.token_urlsafe(12)
        session = Session(actor, self.clock() + self.ttl)
        self.sessions[token] = session
        template, variables = self._categories(session)
        return token, template, variables

    def _session(self, token, actor):
        self._expire()
        session = self.sessions.get(token)
        if session is None or session.actor != actor:
            raise PermissionError('Expired or foreign settings session')
        return session

    async def handle(self, actor, callback_data):
        await self._authorized(actor)
        self._expire()
        action_token = callback_data.removeprefix('set:')
        session = next((s for s in self.sessions.values() if action_token in s.actions and s.actor == actor), None)
        if session is None:
            raise PermissionError('Stale or foreign settings action')
        action = session.actions[action_token]
        session.actions.clear()  # One active screen; replayed saves cannot execute twice.
        op = action[0]
        if op == 'categories':
            return self._categories(session)
        if op == 'category':
            return self._listing(session, *action[1:])
        if op == 'view':
            return self._view(session, action[1])
        if session.key is None:
            raise PermissionError('No active setting')
        setting = self.settings.schema.get(session.key)
        if setting.readonly:
            raise PermissionError('Setting is read-only')
        if op == 'choices':
            return self._choices(session, action[1])
        if op == 'input':
            session.awaiting = True
            return self._page(session, 'input', {'title': setting.title or setting.key}, [[self._button(session, 'Отмена', ('view', session.key))]])
        if op == 'propose':
            return self._preview(session, action[1])
        if op == 'reset':
            return self._preview(session, setting.default, reset=True)
        if op == 'save' and session.has_pending:
            if session.reset:
                await self.settings.reset(session.key, expected_version=session.version)
            else:
                await self.settings.set(session.key, session.pending, expected_version=session.version)
            return self._view(session, session.key)
        raise PermissionError('Invalid settings action')

    async def input(self, actor, session_token, raw):
        await self._authorized(actor)
        session = self._session(session_token, actor)
        if raw is None:
            from .codecs import ValidationError
            raise ValidationError('Expected text or supported media input')
        if not session.awaiting or not session.key:
            raise PermissionError('No active input editor')
        setting = self.settings.schema.get(session.key)
        if setting.readonly:
            raise PermissionError('Setting is read-only')
        value = setting.codec.validate(raw) if isinstance(raw, Media) else setting.codec.parse(raw)
        session.actions.clear()
        return self._preview(session, value)

    async def show(self, chat_id, screen, *, ref=None):
        if self.ui is None:
            raise RuntimeError('Pass UI to SettingsUI to deliver screens')
        template, variables = screen
        return await self.ui.edit(ref, template, variables) if ref else await self.ui.send(chat_id, template, variables)
