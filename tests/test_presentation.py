from __future__ import annotations

import unittest
from tg_templates import *
from tg_settings import *


class PresentationTests(unittest.IsolatedAsyncioTestCase):
    async def test_templateeditor_preview_open_save_and_presentationbinding_refresh(self):
        catalog=Catalog();catalog.register('home',Template('{emoji_save} Hi {name}',buttons=((Button('Save',callback_data='s',style='positive',icon_custom_emoji_id='save'),),)))
        settings=Settings(Schema(Setting('emoji',EmojiCodec(),'123'),Setting('style',ChoiceCodec(['success','danger']),'success'),Setting('image',MediaCodec(),Media('photo','file'))))
        ui=UI(RecordingTransport(),catalog)
        binding=PresentationBinding(settings,ui,lambda v:Theme(emojis={'save':Emoji('✓',v['emoji'])},styles={'positive':v['style']},media={'hero':v['image']}))
        binding.refresh()
        result=ui.render('home',{'name':'Bob'})
        self.assertEqual(result.reply_markup['inline_keyboard'][0][0]['style'],'success')
        await settings.set('style','danger');await settings.set('emoji','456');binding.refresh()
        result=ui.render('home',{'name':'Bob'})
        self.assertEqual(result.reply_markup['inline_keyboard'][0][0]['style'],'danger')
        self.assertIn('456',result.text)
        editor=TemplateEditor(catalog,'home',authorize=lambda _:True,ui=ui)
        token,t,v=await editor.open(7)
        self.assertIn('content',[b.text for row in t.buttons for b in row])
        await editor.settings.set('text','Changed {name}')
        self.assertEqual(catalog.get('home').text,'Changed {name}')
        self.assertEqual(editor.preview({'name':'<&'},renderer=ui.renderer).text,'Changed &lt;&amp;')
        await editor.settings.set('buttons',[])
        await editor.settings.set('media',{'kind':'video','source':'newfile'})
        self.assertEqual(editor.preview({'name':'Bob'}).media.kind,'video')
        with self.assertRaises(ValidationError):await editor.settings.set('media',{'kind':'invalid','source':'x'})


if __name__=='__main__':unittest.main()
