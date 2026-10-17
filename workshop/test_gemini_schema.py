from copy import deepcopy
from django.test import SimpleTestCase
from .ai import SCHEMA, InvalidOutput, gemini_output_schema, parse_output
import json


class GeminiOutputSchemaTests(SimpleTestCase):
    def test_simplifies_nested_limits_without_changing_frozen_schema(self):
        original = deepcopy(SCHEMA)
        schema = gemini_output_schema(SCHEMA)
        self.assertEqual(SCHEMA, original)
        self.assertEqual(schema['required'], SCHEMA['required'])
        self.assertEqual(schema['properties']['grade']['enum'], ['A', 'B', 'C', 'D'])
        self.assertFalse(schema['additionalProperties'])
        self.assertNotIn('maxItems', schema['properties']['mistakes'])
        self.assertNotIn('maxLength', schema['properties']['reason'])
        self.assertEqual(schema['properties']['mistakes']['items']['required'],
                         SCHEMA['properties']['mistakes']['items']['required'])

    def test_full_local_limits_still_reject_invalid_results(self):
        for reason, knowledge in [('', []), ('x' * 12001, []), ('正常', [{'topic':'a', 'observation':'b'}] * 101)]:
            output = {'grade':'C', 'reason':reason, 'suggestions':'', 'knowledge':knowledge, 'mistakes':[]}
            with self.assertRaises(InvalidOutput):
                parse_output(json.dumps(output), output, 'test-model', SCHEMA)
