import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase, override_settings
from .ai import SCHEMA, InvalidOutput, MaterialError, generate
from .forms import SiteConfigForm


@override_settings(AI_API_KEY='deepseek-test-key')
class DeepSeekAdapterTests(SimpleTestCase):
    def setUp(self):
        attachments = Mock()
        attachments.all.return_value = []
        self.job = SimpleNamespace(
            frozen={'adapter':'deepseek', 'model':'configured-model',
                    'url':'https://api.deepseek.com/chat/completions',
                    'global_prompt':'按要求评分', 'subject_prompt':'检查拼写', 'schema':SCHEMA},
            submission=SimpleNamespace(snapshot={'subject':'英语', 'template':{}},
                                       requirements='默写', result='aple', attachments=attachments))
        self.output = {'grade':'C', 'reason':'拼写有误', 'suggestions':'注意双写', 'knowledge':[], 'mistakes':[]}

    def test_admin_form_offers_deepseek(self):
        self.assertIn(('deepseek', 'DeepSeek'), list(SiteConfigForm().fields['ai_adapter'].choices))
        self.assertNotIn('compatible', dict(SiteConfigForm().fields['ai_adapter'].choices))

    @patch('workshop.ai.httpx.post')
    def test_removed_adapter_never_sends_request(self, post):
        self.job.frozen['adapter'] = 'compatible'
        with self.assertRaises(MaterialError):
            generate(self.job)
        post.assert_not_called()

    @patch('workshop.ai.httpx.post')
    def test_json_mode_endpoint_auth_and_validation(self, post):
        post.return_value.json.return_value = {'model':'actual-model', 'choices':[{'message':{'content':json.dumps(self.output)}}]}
        result, model, _, _ = generate(self.job)
        self.assertEqual(result, self.output)
        self.assertEqual(model, 'actual-model')
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], self.job.frozen['url'])
        self.assertEqual(post.call_args.kwargs['headers'], {'Authorization':'Bearer deepseek-test-key'})
        body = post.call_args.kwargs['json']
        self.assertEqual(body['model'], 'configured-model')
        self.assertEqual(body['response_format'], {'type':'json_object'})
        self.assertIn(json.dumps(SCHEMA, ensure_ascii=False), body['messages'][0]['content'])
        self.assertIsInstance(body['messages'][1]['content'], str)
        self.assertIn('aple', body['messages'][1]['content'])

    @patch('workshop.ai.httpx.post')
    def test_invalid_output_is_rejected(self, post):
        post.return_value.json.return_value = {'choices':[{'message':{'content':'{"grade":"A"}'}}]}
        with self.assertRaises(InvalidOutput):
            generate(self.job)

    @patch('workshop.ai.httpx.post')
    def test_missing_key_does_not_call_provider(self, post):
        with self.settings(AI_API_KEY=''), self.assertRaises(PermissionError):
            generate(self.job)
        post.assert_not_called()

    @patch('workshop.ai.httpx.post')
    def test_unsupported_media_does_not_call_provider(self, post):
        self.job.submission.attachments.all.return_value = [SimpleNamespace(pk=1, purpose='result', mime='image/png', name='test.png')]
        with patch('workshop.ai.document_text', return_value=None), self.assertRaises(MaterialError):
            generate(self.job)
        post.assert_not_called()
