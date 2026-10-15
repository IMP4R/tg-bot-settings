from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from tg_templates import Media, RecordingTransport, Renderer, UI
from tg_settings import *


def schema():
    return Schema(Setting('enabled',BoolCodec(),False,'Enabled'),Setting('amount',DecimalCodec(Decimal('0')),Decimal('1.25'),'Amount'),Setting('mode',ChoiceCodec(['one','two']),'one','Mode'),Setting('hero',MediaCodec(),Media('photo','old'),'Hero',category='appearance'),Setting('secret',StringCodec(),'token','Secret',secret=True),Setting('fixed',IntCodec(),1,readonly=True))


class CodecTests(unittest.TestCase):
    def test_validate_parse_encode_decode_all_codecs(self):
        cases=[(BoolCodec(),'yes',True),(IntCodec(0,10),'5',5),(FloatCodec(),'2.5',2.5),(DecimalCodec(),'0.10',Decimal('0.10')),(StringCodec(max_length=10),'123','123'),(ChoiceCodec(['a','b']),'a','a'),(JsonCodec(),'{"x":1}',{'x':1}),(ListCodec(IntCodec()),'[1,2]',[1,2]),(DurationCodec(),'2h',7200),(EmojiCodec(),'123','123'),(URLCodec(),'https://example.org','https://example.org'),(MediaCodec(),'{"kind":"photo","source":"file"}',Media('photo','file')),(TierCodec(),'[{"max":"500","rate":"0.2"}]',[{'max':Decimal('500'),'rate':Decimal('0.2')}])]
        for codec,raw,value in cases:
            with self.subTest(codec=type(codec).__name__):
                self.assertEqual(codec.parse(raw),value)
                self.assertEqual(codec.decode(codec.encode(value)),value)
        for codec,value in [(IntCodec(),True),(FloatCodec(),float('inf')),(DecimalCodec(),0.1),(DecimalCodec(),Decimal('NaN')),(ChoiceCodec(['a']),'b'),(StringCodec(max_length=1),'long'),(EmojiCodec(),'<tag>'),(URLCodec(),'javascript:x'),(ListCodec(IntCodec()),[True]),(TierCodec(),[{'max':Decimal('1'),'rate':Decimal('-1')}])]:
            with self.subTest(codec=type(codec).__name__),self.assertRaises(ValidationError):codec.validate(value)
        self.assertEqual(ListCodec(DecimalCodec()).parse('["0.1"]'),[Decimal('0.1')])
        with self.assertRaises(ValidationError):DurationCodec().parse('bad')
        with self.assertRaises(ValidationError):JsonCodec().parse('NaN')


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_snapshot_get_patch_set_reset_scopes_hooks(self):
        store=MemoryStore();s=Settings(schema(),store);other=Settings(schema(),store,scope='other')
        events=[];unsubscribe=s.on_change(events.append)
        await s.patch({'enabled':True,'amount':Decimal('2.1')},expected_version=0)
        self.assertEqual(s.get('amount'),Decimal('2.1'));self.assertTrue(s.get('enabled'));self.assertFalse(other.get('enabled'))
        self.assertEqual(events[0].version,1)
        unsubscribe()
        with self.assertRaises(ConflictError):await s.set('amount',Decimal('3'),expected_version=0)
        with self.assertRaises(ValidationError):await s.patch({'enabled':False,'amount':0.5})
        self.assertTrue(s.get('enabled'))
        with self.assertRaises(PermissionError):await s.set('fixed',2)
        with self.assertRaises(KeyError):s.get('unknown')
        await s.reset('amount');self.assertEqual(s.get('amount'),Decimal('1.25'))
        self.assertEqual(s.snapshot(redact=True)[1]['secret'],'••••')
        with self.assertRaises(ValidationError):s.schema.register(Setting('enabled',BoolCodec(),False))
        self.assertIn('appearance',s.schema.categories())

    async def test_sqlite_restart_conflicts_cross_validate_and_hook_failure(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'s.db');store=SQLiteStore(path);s=Settings(schema(),store)
            await s.set('amount',Decimal('2.25'));store.close()
            first=SQLiteStore(path);second=SQLiteStore(path)
            s=Settings(schema(),first);other=Settings(schema(),second)
            self.assertEqual(other.get('amount'),Decimal('2.25'))
            version=other.snapshot()[0];await s.set('enabled',True)
            with self.assertRaises(ConflictError):await other.set('enabled',False,expected_version=version)
            def fail(event):raise RuntimeError('notification')
            s.on_change(fail)
            with self.assertRaises(HookError) as ctx:await s.set('secret','changed')
            self.assertEqual(s.get('secret'),'changed');self.assertGreater(ctx.exception.version,0)
            first.close();second.close()
        s=Settings(schema(),cross_validate=lambda values:values['amount']>=Decimal('1'))
        with self.assertRaises(ValidationError):await s.set('amount',Decimal('0.5'))


class UIFlowTests(unittest.IsolatedAsyncioTestCase):
    def callback(self,screen,text):
        template,variables=screen
        Renderer().render(template,variables)
        return next(b.callback_data for row in template.buttons for b in row if b.text==text)

    async def test_settingsui_categories_view_propose_save_reset_cancel_auth(self):
        s=Settings(schema());tr=RecordingTransport();admin=SettingsUI(s,authorize=lambda actor:actor==7,ui=UI(tr))
        token,t,v=await admin.open(7);screen=(t,v)
        screen=await admin.handle(7,self.callback(screen,'general'))
        screen=await admin.handle(7,self.callback(screen,'Enabled'))
        screen=await admin.handle(7,self.callback(screen,'Включить'))
        self.assertFalse(s.get('enabled'))
        save=self.callback(screen,'Сохранить')
        with self.assertRaises(PermissionError):await admin.handle(8,save)
        screen=await admin.handle(7,save);self.assertTrue(s.get('enabled'))
        with self.assertRaises(PermissionError):await admin.handle(7,save)
        screen=await admin.handle(7,self.callback(screen,'Сбросить'))
        screen=await admin.handle(7,self.callback(screen,'Отмена'));self.assertTrue(s.get('enabled'))
        screen=await admin.handle(7,self.callback(screen,'Сбросить'))
        screen=await admin.handle(7,self.callback(screen,'Сохранить'));self.assertFalse(s.get('enabled'))
        await admin.show(7,screen);self.assertEqual(tr.calls[-1][0],'sendMessage')
        with self.assertRaises(PermissionError):await admin.open(8)

    async def test_input_preview_conflict_choice_media_secret_expiry(self):
        s=Settings(schema());now=[0];admin=SettingsUI(s,authorize=lambda _:True,clock=lambda:now[0],ttl=10,page_size=2)
        token,t,v=await admin.open(7);screen=(t,v)
        screen=await admin.handle(7,self.callback(screen,'general'))
        screen=await admin.handle(7,self.callback(screen,'Amount'))
        screen=await admin.handle(7,self.callback(screen,'Изменить'))
        screen=await admin.input(7,token,'3.25')
        await s.set('enabled',True)
        with self.assertRaises(ConflictError):await admin.handle(7,self.callback(screen,'Сохранить'))
        token,t,v=await admin.open(7);screen=await admin.handle(7,self.callback((t,v),'general'))
        screen=await admin.handle(7,self.callback(screen,'Далее'))
        screen=await admin.handle(7,self.callback(screen,'Mode'))
        screen=await admin.handle(7,self.callback(screen,'Выбрать'))
        screen=await admin.handle(7,self.callback(screen,'two'))
        screen=await admin.handle(7,self.callback(screen,'Сохранить'));self.assertEqual(s.get('mode'),'two')
        token,t,v=await admin.open(7);screen=await admin.handle(7,self.callback((t,v),'appearance'))
        screen=await admin.handle(7,self.callback(screen,'Hero'))
        self.assertEqual(screen[0].media,Media('photo','old'))
        screen=await admin.handle(7,self.callback(screen,'Изменить'))
        screen=await admin.input(7,token,Media('video','new'))
        screen=await admin.handle(7,self.callback(screen,'Сохранить'));self.assertEqual(s.get('hero'),Media('video','new'))
        now[0]=11
        with self.assertRaises(PermissionError):await admin.handle(7,self.callback(screen,'Изменить'))


if __name__=='__main__':unittest.main()
