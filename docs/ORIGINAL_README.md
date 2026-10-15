# tg-bot-settings

Typed runtime configuration и шаблонные административные экраны. Bool/int/float/Decimal/string/choice/json/list/duration/tier/URL/media/custom emoji; extensible codecs; schema metadata; Memory/SQLite scopes; atomic patches/CAS, cross-field validation и after-commit hooks.

SettingsUI рисует **только через tg_templates**, включая preview/confirm/cancel/reset и actor-bound sessions. TemplateEditor переиспользует редактор для runtime контента. PresentationBinding связывает настройки styles/emojis/images с Theme.

Документация: [docs/API.md](docs/API.md). Сквозной бот: [../examples/universal_bot.py](../examples/universal_bot.py).

```python
from tg_settings import *
s = Settings(Schema(Setting("enabled", BoolCodec(), True)))
await s.set("enabled", False)
assert s.get("enabled") is False
```

Requires tg-bot-templates; никакого обязательного aiogram. Secrets masked, не encrypted. UI sessions in-memory; multiworker требует sticky routing.
