# A pdfkit alternative for HTML-to-PDF calls

This is a small compatibility module, not the official pdfkit package. Replace
`import pdfkit` with `import pdfkit_compat as pdfkit` to send `from_string` or
`from_url` calls to the existing [hiver/bulk-website-screenshots Actor](https://apify.com/hiver/bulk-website-screenshots?utm_source=github&utm_campaign=pdfkit-alternative).
Chromium renders the PDF; no local wkhtmltopdf installation is needed.

This is a paid cloud call, not a free local renderer. HTML and URLs go to Apify.
Do not send secrets, private documents or sensitive customer records without
first reviewing Apify's handling and retention policies. Use your own Apify
account and token; this repository contains none.

## Install and use

Clone this repository, install its dependency and run your script from the repo
(or copy `pdfkit_compat.py` next to your script):

```sh
git clone https://github.com/hiver-data/pdfkit-alternative.git
cd pdfkit-alternative
python -m pip install -r requirements.txt
```

Set `APIFY_TOKEN` in your environment using your own secret manager. The module
also accepts `token=` or an injected `apify_client.ApifyClient` via `client=`.
No request is made until you call a rendering function.

```python
import pdfkit_compat as pdfkit

options = {
    'page-size': 'Letter',
    'orientation': 'Landscape',
    'margin-top': '0.5in',
    'margin-right': '0.5in',
    'margin-bottom': '0.5in',
    'margin-left': '0.5in',
    'print-media-type': None,
}

pdfkit.from_string('<h1>Invoice</h1><p>Paid</p>', 'invoice.pdf', options)
pdfkit.from_url('https://en.wikipedia.org/wiki/Cat', 'cat.pdf', options)
# Saving returns True. False/None as the output path returns PDF bytes.
pdf_bytes = pdfkit.from_string('<p>Receipt</p>', False, options)
```

Each call starts exactly one Actor run. All calls set `max_total_charge_usd=1.00`,
1024 MB memory, a 300-second run timeout and a 330-second wait. Change the charge
cap explicitly if needed:

```python
pdfkit.from_string('<p>Receipt</p>', 'receipt.pdf', max_total_charge_usd=0.02)
```

A charge cap is a platform limit, not a price quote or a promise the entire cap
will be spent. The module does not automatically retry billed calls. An
unfinished/failed run raises `PDFKitError`; inspect that run before calling
again. Existing output files are replaced atomically only after the complete
PDF has been fetched. Output directories must already exist.

## Supported subset and differences

- `from_string(html, path, options)` and `from_url(url, path, options)` accept
  one nonempty string. `from_file`, lists, CSS file arguments, headers/footers,
  cookies, authentication, local-file access and pdfkit `configuration` are not
  supported. Unknown options raise before starting a paid run.
- `page-size` maps to `pageSize`: A4, Letter, Legal, A3, A5 or Tabloid.
- `orientation` maps to `landscape`: Portrait or Landscape.
- `margin-top/right/bottom/left` map to the Actor's four-side `margin` object.
  Nonnegative mm/cm/in/px/pt work; unitless numeric margins are millimetres.
- `print-media-type` maps to `format: pdf`: Chromium's PDF output always uses
  print CSS, whether the flag is omitted or passed as None/empty string/True.
  Screen-media rendering is not supported.
- Defaults are the Actor's A4 portrait, zero margins, printed backgrounds —
  NOT wkhtmltopdf's default margins. Chromium layout/fonts, JavaScript and
  pagination are not guaranteed to match wkhtmltopdf byte-for-byte.
- HTML has a 2 MiB UTF-8 limit, uses about:blank and cannot read local files.
  Relative external resource URLs need absolute URLs or an HTML base element.
  URLs must be http(s) without embedded credentials.
- JavaScript gets the Actor's normal loading/delay behaviour; this is not a
  guarantee arbitrary asynchronous applications have finished rendering.

For HTML lists and mixed HTML + URL batches, use the Actor directly rather than
calling this scalar shim in a loop. One run can use:

```json
{
  "html": ["<h1>Invoice</h1>", "<h1>Receipt</h1>"],
  "urls": ["https://en.wikipedia.org/wiki/Cat"],
  "format": "pdf",
  "pageSize": "Letter",
  "landscape": true,
  "margin": "0.5in"
}
```

## Dated evidence and price

Board run `xCcB7tEdn6snKVWj8` (2026-10-14, build 0.1.19) returned 4/4 successful
PDF files from three HTML strings plus Wikipedia Cat, with zero error rows and
four `screenshot-taken` charge events. Every page in all four downloaded files
has MediaBox [0, 0, 792, 612] (Letter landscape). The Promise/fetch/arrow-script
HTML printed MODERN_JS_PRINTED. Run time: 36.568 seconds at 1024 MB. This is one
cloud smoke, not a long-term reliability or competitor bench result.

Public pricing checked on 2026-10-14: $0.80 per 1,000 successfully stored files
on the free tier, plus Apify's $0.00005 per-run-per-GB start event; paid tiers are
lower. Failed captures have no `screenshot-taken` event (the start event still
applies). Check the Store page for current pricing. The board's $0.0023973 run
usage is platform credit usage, not the customer's per-event bill.

## Offline tests

```sh
python -m unittest -v
```

The tests make no Apify runs or HTTP requests. They use the board's full exported
input/rows and the actual first HTML PDF. The URL-success transport test
explicitly substitutes that small invoice PDF for a download; it is not the
cloud Cat PDF. The exact Cat row separately tests detection of truncated output.
The four full cloud PDFs were independently downloaded and checked in the
issue's cloud gate. Install requirements before running the 21 tests: they
exercise the installed SDK's real ActorClient.call signature, dataset pagination
and binary-record deserializer without HTTP. The fake client also rejects unknown
SDK keywords. Synthetic run metadata is labelled in the test; the row and PDF
come from the board export.

Both apify-client 3.3.0 and 2.5.1 were checked in fresh virtual environments on
2026-10-14: `pip install -r requirements.txt apify-client==VERSION`, all 21 tests
passed with no skips, and a fake-token `from_string('<h1>x</h1>', False,
{'page-size': 'Letter'})` reached Apify's HTTP 401 authentication error (no
TypeError, no billed run). The shim uses timedelta timeouts and normalizes the
Run model on 3.x; 2.x uses seconds and a dictionary. Requirements allow >=2.5,<4.

This module is MIT licensed. Fixtures document our own board run; no pdfkit
source code is copied. No affiliation with pdfkit or wkhtmltopdf.
