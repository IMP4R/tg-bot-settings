from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from tg_templates import Catalog, Renderer, Template, Theme, template_from_dict

from .codecs import ChoiceCodec, JsonCodec, StringCodec
from .core import Schema, Setting, Settings
from .ui import SettingsUI


class TemplateEditor:
    """Reuse the typed settings editor for runtime content instead of a second FSM.

    One editor instance per template/locale. Defaults come from Catalog; saving
    affects only the selected variant. Bot-specific templates aren't imported.
    """
    def __init__(self, catalog: Catalog, key: str, *, locale=None, authorize, ui=None):
        self.catalog, self.key, self.locale = catalog, key, locale or catalog.default_locale
        template = catalog.get(key, self.locale)
        data = asdict(template)
        settings = [
            Setting('text',StringCodec(),data['text'],'Текст',category='content'),
            Setting('parse_mode',ChoiceCodec(['HTML','plain']),data['parse_mode'] or 'plain','Формат',category='content'),
            Setting('media',JsonCodec(),data['media'],'Медиа (null / kind+source)',category='content'),
            Setting('buttons',JsonCodec(),data['buttons'],'Inline-кнопки',category='keyboard'),
            Setting('reply_buttons',JsonCodec(),data['reply_buttons'],'Reply-кнопки',category='keyboard'),
        ]
        from .codecs import BoolCodec
        settings.append(Setting('remove_keyboard',BoolCodec(),template.remove_keyboard,'Убрать клавиатуру',category='keyboard'))
        def validate(values):
            candidate = self._candidate(values)
            # Validate structure early. Content variables may not yet exist, so
            # rendering must occur through preview with caller's sample variables.
            try:
                template_from_dict(candidate)
            except (ValueError, TypeError):
                return False
            if sum([bool(candidate['buttons']),bool(candidate['reply_buttons']),candidate['remove_keyboard']]) > 1:
                return False
            return True
        self.settings = Settings(Schema(*settings),cross_validate=validate)
        self.settings.on_change(self._save)
        self.controller = SettingsUI(self.settings,authorize=authorize,ui=ui)

    @staticmethod
    def _candidate(values):
        return {**values,'parse_mode':None if values['parse_mode']=='plain' else values['parse_mode']}

    def _save(self, change):
        self.catalog.update(self.key,self._candidate(self.settings.snapshot()[1]),locale=self.locale)

    def preview(self, variables=None, *, renderer=None):
        candidate=template_from_dict(self._candidate(self.settings.snapshot()[1]))
        return (renderer or Renderer()).render(candidate,variables)

    async def open(self, actor):
        return await self.controller.open(actor)


class PresentationBinding:
    """Refresh UI theme from typed settings on demand. No global cache/singleton.

    theme_factory(values) -> Theme can choose per-user/per-bot media, emojis and
    style aliases. refresh() is explicit, so a failed refresh never pretends the
    settings write was rolled back.
    """
    def __init__(self, settings: Settings, ui, theme_factory: Callable):
        self.settings, self.ui, self.theme_factory = settings, ui, theme_factory

    def refresh(self):
        theme=self.theme_factory(self.settings.snapshot()[1])
        if not isinstance(theme,Theme):
            raise TypeError('theme_factory must return Theme')
        self.ui.renderer=Renderer(theme)
        return theme
