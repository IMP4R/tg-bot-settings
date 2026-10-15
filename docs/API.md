# tg_settings: единое место для настроек

## Объявление и чтение
```python
from decimal import Decimal
from tg_settings import *
settings = Settings(Schema(
    Setting('shop.enabled', BoolCodec(), True, 'Магазин', category='shop'),
    Setting('shop.price', DecimalCodec(minimum=Decimal('0.01')),
            Decimal('2.50'), 'Цена', category='shop'),
), SQLiteStore('settings.sqlite'), scope='bot:42')
price = settings.get('shop.price')               # Decimal, всегда
await settings.set('shop.price', Decimal('3.25'))
version, values = settings.snapshot()
await settings.patch({'shop.enabled': False}, expected_version=version)
```
`get/snapshot` синхронные, `set/patch/reset` async для hooks. SQLite local calls синхронные и пригодны для небольших config; для тяжелых/remote хранилищ реализуйте Store contract, не блокируйте event loop сетевым I/O. Нет stale TTL-cache: каждый snapshot читает store. MemoryStore thread-lock, SQLite BEGIN IMMEDIATE + CAS. Один SQLiteStore используйте в своем потоке; несколько процессов — отдельные соединения к одному файлу. Parent dir создайте самостоятельно.

## Codecs
Bool, Int (не bool), Float finite, Decimal finite/no float, String length/pattern, Choice, Json finite, List(item codec), Duration (секунды/5m/2h/1d), Emoji numeric ID, Media, URL, Tier (ascending Decimal max/rate). Decimal хранится строкой через свой codec, но обычная String '123' никогда не становится int.
Все defaults проверяются при register. Кастомный Codec implements validate/parse/encode/decode и может иметь `editor`. `Setting.validate` добавляет доменную проверку. `cross_validate(values)` проверяет патч целиком; CAS всегда привязан к проверенному snapshot. Неизвестные ключи отклоняются. Mutable values возвращаются копиями.

## Runtime
`on_change(hook)` → unsubscribe. Change содержит old/new/scope/version. Для secret old/new=None; UI/snapshot(redact=True) скрывают значение. Hooks после commit: HookError означает **сохранено**, но уведомление не доставлено; повторять write нельзя. Secrets не зашифрованы в SQLite, это masking, не vault. Ключи и названия не должны содержать секреты. readonly запрещает set/reset.

## Экраны
```python
admin = SettingsUI(settings, authorize=lambda actor: actor in admin_ids, ui=ui)
session_id, template, variables = await admin.open(actor)
await admin.show(chat_id, (template, variables))
screen = await admin.handle(actor, callback_data)
screen = await admin.input(actor, session_id, '3.25')
```
Каждая операция авторизуется заново. Session actor-bound, TTL, random callbacks, stale/replayed actions отклоняются. После рестарта sessions истекают — откройте панель снова. Multiple workers требуют sticky sessions/shared session layer (не реализовано). Actor/chat context должен проверять host; не принимайте actor от клиента без Telegram ingress auth.
Categories → pagination list → view → bool/choice/input/media → preview → confirm/cancel/reset. Readonly нет write-кнопок; secret никогда не показывается. Json/list/tier редактируются JSON; отдельный drag/drop/reorder/list-item wizard не реализован. Category list пока без pagination; fields/choices paginated. Media можно передать как `Media(...)` из event adapter либо JSON. В complete demo фото/видео/документ из Telegram переводятся в Media.

## Настройки оформления
`PresentationBinding(settings, ui, theme_factory)` + `.refresh()` строит Theme из типизированных значений. После save можно refresh перед следующим render: styles/emojis/media работают через единый renderer, без aiogram.
`TemplateEditor(catalog, key, locale=..., authorize=..., ui=...)` повторно использует SettingsUI для текста/parse_mode/media/inline/reply/remove. Изменяет только выбранный template+locale. `.preview(sample_variables, renderer=...)` дает RenderedMessage. Ввод buttons пока JSON. Catalog overrides SQLite обеспечивают persistence. Schema runtime editor использует in-memory settings snapshot; при новом экземпляре читает текущий Catalog. Hooks не дают транзакции сразу между Settings store и Catalog store; production для этого требует общей транзакционной модели.

## Customization
SettingsUI `templates={screen_name: text}` меняет categories/list/view/input/preview; variables экранируются templates renderer. `ui.renderer.theme` задает стили/emoji/media. Нет сообщений через raw aiogram внутри settings.
