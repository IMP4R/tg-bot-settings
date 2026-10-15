from __future__ import annotations
import asyncio
from decimal import Decimal
from tg_settings import Setting, Schema, Settings, DecimalCodec, SettingsUI
from tg_templates import Renderer
async def main():
    settings=Settings(Schema(Setting("price",DecimalCodec(),Decimal("2.50"),"Price")))
    admin=SettingsUI(settings,authorize=lambda actor:actor==7)
    token,template,variables=await admin.open(7)
    print(Renderer().render(template,variables))
    await settings.set("price",Decimal("3.25"))
    print(settings.snapshot())
if __name__=="__main__":asyncio.run(main())
