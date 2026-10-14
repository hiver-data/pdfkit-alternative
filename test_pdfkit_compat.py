import copy
from decimal import Decimal
from datetime import timedelta
import inspect
from types import SimpleNamespace
from apify_client import ApifyClient
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pdfkit_compat as pdfkit

SDK_CALL_SIGNATURE = inspect.signature(type(ApifyClient("offline-fixture-not-a-token").actor("fixture")).call)

FIXTURES = Path(__file__).parent / 'fixtures'
EXPORT = json.loads((FIXTURES / 'board-run.json').read_text())['run']
ROWS = EXPORT['rows']
HTML_ROW = next(r for r in ROWS if r.get('htmlIndex') == 0)
URL_ROW = next(r for r in ROWS if r.get('url'))
BLOB = (FIXTURES / HTML_ROW['imageKey']).read_bytes()
OPTIONS = {'page-size': 'Letter', 'orientation': 'Landscape', 'margin-top': '0.5in',
           'margin-right': '0.5in', 'margin-bottom': '0.5in', 'margin-left': '0.5in', 'print-media-type': None}


class FakeClient:
    """No Apify/network calls. Exact board row where applicable, fake SDK transport."""
    def __init__(self, rows=None, blob=BLOB, status='SUCCEEDED'):
        self.rows = copy.deepcopy([HTML_ROW] if rows is None else rows)
        self.blob, self.status = blob, status
        self.calls, self.reads = [], []

    def actor(self, actor):
        self.actor_id = actor
        return self

    def call(self, **kwargs):
        SDK_CALL_SIGNATURE.bind(self, **kwargs)  # Reject every unknown SDK kwarg.
        self.calls.append(kwargs)
        return {'status': self.status, 'defaultDatasetId': 'fixture-dataset', 'defaultKeyValueStoreId': 'fixture-store'}

    def dataset(self, dataset):
        self.reads.append(('dataset', dataset))
        return self

    def iterate_items(self):
        return iter(self.rows)

    def key_value_store(self, store):
        self.reads.append(('store', store))
        return self

    def get_record(self, key):
        self.reads.append(('key', key))
        return {'value': self.blob}


FakeClient.call.__signature__ = SDK_CALL_SIGNATURE


class ContractTests(unittest.TestCase):
    def test_full_board_export_gate(self):
        self.assertEqual(EXPORT['status'], 'SUCCEEDED')
        self.assertEqual(len(ROWS), 4)
        self.assertEqual(EXPORT['charged_events']['screenshot-taken'], 4)
        self.assertEqual(EXPORT['error_rows'], 0)
        self.assertEqual([r.get('htmlIndex') for r in ROWS if r.get('source') == 'html'], [0, 1, 2])
        for row in ROWS:
            self.assertEqual((row['status'], row['format'], row['width'], row['height'], row['pageSize'], row['landscape']), ('ok', 'pdf', 792, 612, 'Letter', True))
        self.assertEqual(URL_ROW['pageHeight'], 34232)

    def test_exact_real_html_row_and_record_return_bytes(self):
        fake = FakeClient()
        result = pdfkit.from_string(EXPORT['input']['html'][0], False, OPTIONS, client=fake)
        self.assertEqual(result, BLOB)
        sent = fake.calls[0]
        self.assertEqual(sent['run_input']['html'], EXPORT['input']['html'][0])
        for key in ('format', 'pageSize', 'landscape', 'printBackground'):
            self.assertEqual(sent['run_input'][key], EXPORT['input'][key])
        self.assertEqual(sent['run_input']['margin'], dict.fromkeys(('top', 'right', 'bottom', 'left'), '0.5in'))
        self.assertEqual(sent['max_total_charge_usd'], Decimal('1.0'))
        self.assertEqual(sent['memory_mbytes'], 1024)
        if 'run_timeout' in SDK_CALL_SIGNATURE.parameters:
            self.assertEqual((sent['run_timeout'], sent['wait_duration']), (timedelta(seconds=300), timedelta(seconds=330)))
        else:
            self.assertEqual((sent['timeout_secs'], sent['wait_secs']), (300, 330))
        self.assertEqual(fake.actor_id, pdfkit.ACTOR_ID)
        self.assertIn(('key', HTML_ROW['imageKey']), fake.reads)

    def test_atomic_path_return_true(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'invoice.pdf'
            path.write_bytes(b'old file')
            self.assertIs(pdfkit.from_string('hello', path, client=FakeClient()), True)
            self.assertEqual(path.read_bytes(), BLOB)
            self.assertEqual(list(Path(d).iterdir()), [path])

    def test_none_path_returns_bytes(self):
        self.assertEqual(pdfkit.from_string('hello', None, client=FakeClient()), BLOB)

    def test_from_url_exact_real_row_detects_truncated_record(self):
        fake = FakeClient([URL_ROW])
        with self.assertRaisesRegex(pdfkit.PDFKitError, 'complete PDF'):
            pdfkit.from_url(URL_ROW['url'], False, OPTIONS, client=fake)
        self.assertEqual(fake.calls[0]['run_input']['urls'], [URL_ROW['url']])
        self.assertIn(('key', URL_ROW['imageKey']), fake.reads)

    def test_from_url_success_with_explicit_transport_fixture(self):
        # Real URL row shape, but small real invoice bytes stand in for the Cat
        # download. This is NOT the cloud Cat artifact or a cloud acceptance claim.
        row = dict(URL_ROW, bytes=len(BLOB))
        fake = FakeClient([row])
        self.assertEqual(pdfkit.from_url(URL_ROW['url'], client=fake), BLOB)
        self.assertEqual(len(fake.calls), 1)

    def test_all_page_sizes_and_orientations(self):
        for size in pdfkit.PAGE_SIZES.values():
            for orientation in ('Landscape', 'Portrait'):
                with self.subTest(size=size, orientation=orientation):
                    mapped = pdfkit._options({'page-size': size.lower(), 'orientation': orientation})
                    self.assertEqual(mapped['pageSize'], size)
                    self.assertEqual(mapped['landscape'], orientation == 'Landscape')

    def test_default_and_numeric_margins(self):
        self.assertEqual(pdfkit._options(None)['pageSize'], 'A4')
        self.assertEqual(pdfkit._options({'margin-top': 10})['margin']['top'], '10mm')
        self.assertEqual(pdfkit._options({'margin-left': '1in'})['margin']['right'], '0mm')

    def test_unsupported_and_bad_options_fail_before_call(self):
        cases = ({'header-html': 'file:///secret'}, {'page-size': 'B4'}, {'orientation': 'sideways'},
                 {'margin-top': '-1in'}, {'margin-left': 'calc(1in)'}, {'margin-right': True},
                 {'print-media-type': False}, {'enable-local-file-access': None})
        for options in cases:
            with self.subTest(options=options):
                fake = FakeClient()
                with self.assertRaises(ValueError):
                    pdfkit.from_string('hello', options=options, client=fake)
                self.assertEqual(fake.calls, [])

    def test_caps_validated_before_call(self):
        for cap in (0, -1, True, float('inf'), float('nan'), 'not-a-number'):
            fake = FakeClient()
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                pdfkit.from_string('hello', client=fake, max_total_charge_usd=cap)
            self.assertEqual(fake.calls, [])
        fake = FakeClient()
        pdfkit.from_string('hello', client=fake, max_total_charge_usd='0.02')
        self.assertEqual(fake.calls[0]['max_total_charge_usd'], Decimal('0.02'))

    def test_empty_oversized_and_batch_html_rejected_before_bill(self):
        for html in ('', '  ', 'x' * (2 * 1024 * 1024 + 1), ['one', 'two']):
            fake = FakeClient()
            with self.assertRaises(ValueError):
                pdfkit.from_string(html, client=fake)
            self.assertEqual(fake.calls, [])

    def test_file_and_credentials_urls_rejected_before_bill(self):
        for url in ('file:///etc/passwd', 'data:text/html,hi', 'https://user:pass@example.org', 'not a url'):
            fake = FakeClient()
            with self.assertRaises(ValueError):
                pdfkit.from_url(url, client=fake)
            self.assertEqual(fake.calls, [])

    def test_missing_token_before_import_sdk(self):
        with patch.dict('os.environ', {}, clear=True), self.assertRaisesRegex(ValueError, 'APIFY_TOKEN'):
            pdfkit.from_string('hello')

    def test_failed_and_unfinished_runs_never_retried(self):
        for status in ('FAILED', 'TIMED-OUT', 'RUNNING', 'ABORTED'):
            fake = FakeClient(status=status)
            with self.assertRaises(pdfkit.PDFKitError):
                pdfkit.from_string('hello', client=fake)
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(fake.reads, [])

    def test_error_missing_mixed_and_wrong_source_rows(self):
        cases = ([], ROWS, [dict(HTML_ROW, status='error', error='timeout')], [URL_ROW],
                 [dict(HTML_ROW, imageKey=None)], [dict(HTML_ROW, htmlIndex=1)])
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(pdfkit.PDFKitError):
                pdfkit.from_string('hello', client=FakeClient(rows))

    def test_failed_download_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'invoice.pdf'
            path.write_bytes(b'previous')
            with self.assertRaises(pdfkit.PDFKitError):
                pdfkit.from_string('hello', path, client=FakeClient(blob=b'not pdf'))
            self.assertEqual(path.read_bytes(), b'previous')

    def test_bad_path_before_bill(self):
        with tempfile.TemporaryDirectory() as d:
            fake = FakeClient()
            with self.assertRaises(ValueError):
                pdfkit.from_string('hello', Path(d) / 'missing' / 'out.pdf', client=fake)
            self.assertEqual(fake.calls, [])

    def test_fake_rejects_unknown_sdk_kwargs(self):
        with self.assertRaises(TypeError):
            FakeClient().call(not_a_real_sdk_option=True)

    def test_real_sdk_call_reaches_http_transport(self):
        # Real ActorClient.call/start; only HTTP is replaced, never the signature.
        sdk = ApifyClient('offline-fixture-not-a-token')
        with patch.object(sdk.http_client, 'call', side_effect=RuntimeError('HTTP boundary')) as http:
            with self.assertRaisesRegex(RuntimeError, 'HTTP boundary'):
                pdfkit.from_string('<h1>x</h1>', False, {'page-size': 'Letter'}, client=sdk)
        self.assertEqual(http.call_count, 1)
        self.assertEqual(http.call_args.kwargs['method'], 'POST')
        self.assertEqual(http.call_args.kwargs['params']['timeout'], 300)
        self.assertEqual(http.call_args.kwargs['params']['memory'], 1024)

    def test_real_sdk_output_path(self):
        # Synthetic run metadata; real board row/PDF. No cloud-success claim.
        sdk = ApifyClient('offline-fixture-not-a-token')
        actor_type = type(sdk.actor('fixture'))
        run_type = type(sdk.run('fixture'))
        run = {'id': 'fixture-run', 'status': 'SUCCEEDED',
               'defaultDatasetId': 'fixture-dataset', 'defaultKeyValueStoreId': 'fixture-store'}
        if 'run_timeout' in SDK_CALL_SIGNATURE.parameters:
            model = inspect.unwrap(run_type.wait_for_finish).__globals__['Run']
            run = model.model_construct(id='fixture-run', status='SUCCEEDED',
                                      default_dataset_id='fixture-dataset',
                                      default_key_value_store_id='fixture-store')
        headers = {'x-apify-pagination-total': '1', 'x-apify-pagination-offset': '0',
                   'x-apify-pagination-count': '1', 'x-apify-pagination-limit': '1000',
                   'x-apify-pagination-desc': 'false'}
        page = SimpleNamespace(status_code=200, headers=headers, json=lambda: [HTML_ROW])
        pdf = SimpleNamespace(status_code=200, content=BLOB, headers={'content-type': 'application/pdf'})
        def respond(**kwargs):
            if kwargs['url'].endswith('/items'):
                if kwargs['params'].get('offset', 0) == 0:
                    return page
                return SimpleNamespace(status_code=200, headers=dict(headers, **{'x-apify-pagination-count': '0', 'x-apify-pagination-offset': '1'}), json=lambda: [])
            return pdf
        with patch.object(actor_type, 'start', return_value=run), \
             patch.object(run_type, 'wait_for_finish', return_value=run) as wait, \
             patch.object(sdk.http_client, 'call', side_effect=respond) as http:
            self.assertEqual(pdfkit.from_string('hello', client=sdk), BLOB)
        self.assertEqual(http.call_count, 3 if 'run_timeout' in SDK_CALL_SIGNATURE.parameters else 2)
        timing = wait.call_args.kwargs
        if 'run_timeout' in SDK_CALL_SIGNATURE.parameters:
            self.assertEqual(timing['wait_duration'], timedelta(seconds=330))
        else:
            self.assertEqual(timing['wait_secs'], 330)

    def test_sdk_get_record_returns_binary(self):
        sdk = ApifyClient('offline-fixture-not-a-token')
        response = SimpleNamespace(status_code=200, content=BLOB, headers={'content-type': 'application/pdf'})
        with patch.object(sdk.http_client, 'call', return_value=response):
            record = sdk.key_value_store('fixture-store').get_record(HTML_ROW['imageKey'])
        self.assertEqual(record['value'], BLOB)
        self.assertEqual(record['content_type'], 'application/pdf')


if __name__ == '__main__':
    unittest.main()
