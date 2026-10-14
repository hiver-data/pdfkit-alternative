"""A small, paid-cloud subset of pdfkit backed by hiver's existing Actor.

Not an official pdfkit package or a local wkhtmltopdf replacement. Import swap:
    import pdfkit_compat as pdfkit
No network request is made on import. Unsupported options fail before billing.
"""
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

ACTOR_ID = "hiver/bulk-website-screenshots"
PAGE_SIZES = {s.lower(): s for s in ("A4", "Letter", "Legal", "A3", "A5", "Tabloid")}
SUPPORTED_OPTIONS = {"page-size", "orientation", "margin-top", "margin-right", "margin-bottom", "margin-left", "print-media-type"}


class PDFKitError(RuntimeError):
    """The Actor or its stored output could not supply the requested PDF."""


def _length(value):
    if isinstance(value, bool):
        raise ValueError("Margins must be nonnegative lengths, not booleans")
    value = str(value).strip()
    if re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)", value):
        value += "mm"  # wkhtmltopdf interprets unitless margins as millimetres.
    if not re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)(?:mm|cm|in|px|pt)", value):
        raise ValueError("Margins accept nonnegative mm, cm, in, px or pt lengths")
    return value


def _options(options):
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise TypeError("options must be a dictionary")
    unknown = set(options) - SUPPORTED_OPTIONS
    if unknown:
        raise ValueError("Unsupported pdfkit options: " + ", ".join(sorted(map(str, unknown))))
    size = PAGE_SIZES.get(str(options.get("page-size", "A4")).lower())
    if size is None:
        raise ValueError("page-size must be A4, Letter, Legal, A3, A5 or Tabloid")
    orientation = str(options.get("orientation", "Portrait")).lower()
    if orientation not in {"portrait", "landscape"}:
        raise ValueError("orientation must be Portrait or Landscape")
    if "print-media-type" in options and options["print-media-type"] not in (None, "", True):
        raise ValueError("print-media-type is a flag (None, empty string or True); screen-media mode is unsupported")
    return {"format": "pdf", "pageSize": size, "landscape": orientation == "landscape",
            "margin": {side: _length(options.get("margin-" + side, "0")) for side in ("top", "right", "bottom", "left")},
            "printBackground": True}


def _charge_cap(value):
    if isinstance(value, bool):
        raise ValueError("max_total_charge_usd must be a positive finite number")
    try:
        cap = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("max_total_charge_usd must be a positive finite number") from None
    if not cap.is_finite() or cap <= 0:
        raise ValueError("max_total_charge_usd must be a positive finite number")
    return cap


def _convert(source, value, output_path, options, *, token, client, max_total_charge_usd):
    inp = _options(options)
    cap = _charge_cap(max_total_charge_usd)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(source + " must be a nonempty string; use the Actor directly for batch inputs")
    if source == "html":
        if len(value.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("HTML exceeds the Actor's 2 MiB UTF-8 limit")
        inp["html"] = value
    else:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("from_url requires an http(s) URL without embedded credentials")
        inp["urls"] = [value]
    destination = None
    if output_path is not False and output_path is not None:
        destination = Path(output_path)
        if not destination.parent.is_dir() or destination.is_dir():
            raise ValueError("The output's parent must exist and output must not be a directory")
    if client is None:
        token = token or os.environ.get("APIFY_TOKEN")
        if not token:
            raise ValueError("Set APIFY_TOKEN or pass token=your_own_apify_token")
        from apify_client import ApifyClient
        client = ApifyClient(token)
    # One call only: failed/unfinished runs are never retried as new billed runs.
    run = client.actor(ACTOR_ID).call(run_input=inp, max_total_charge_usd=cap,
                                     memory_mbytes=1024, timeout_secs=300, wait_secs=330)
    if not run or run.get("status") != "SUCCEEDED":
        status = (run or {}).get("status", "missing response")
        raise PDFKitError("Actor run did not succeed: " + str(status) + "; inspect it before starting another run")
    dataset = run.get("defaultDatasetId")
    store = run.get("defaultKeyValueStoreId")
    if not dataset or not store:
        raise PDFKitError("Run response is missing output storage IDs")
    rows = list(client.dataset(dataset).iterate_items())
    if len(rows) != 1:
        raise PDFKitError("Expected one output row, received " + str(len(rows)))
    row = rows[0]
    if row.get("status") != "ok" or row.get("error") or row.get("format") != "pdf":
        raise PDFKitError("PDF render failed: " + str(row.get("error") or row.get("status")))
    if source == "html":
        matched = row.get("source") == "html" and row.get("htmlIndex") == 0 and row.get("url") is None
    else:
        matched = row.get("url") == value
    if not matched:
        raise PDFKitError("Output row does not match the requested source")
    key = row.get("imageKey")
    if not isinstance(key, str) or not key.endswith(".pdf"):
        raise PDFKitError("Output row is missing its PDF imageKey")
    record = client.key_value_store(store).get_record(key)
    blob = (record or {}).get("value")
    if not isinstance(blob, (bytes, bytearray)) or not blob.startswith(b"%PDF-") or len(blob) != row.get("bytes"):
        raise PDFKitError("Stored output is not the complete PDF described by its row")
    blob = bytes(blob)
    if destination is None:
        return blob
    # Atomic replacement leaves an existing file untouched on failed download/write.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix="." + destination.name + ".", delete=False) as f:
            temporary = Path(f.name)
            f.write(blob)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def from_string(html, output_path=False, options=None, *, token=None, client=None, max_total_charge_usd=1.00):
    """Return True after saving, or PDF bytes when output_path is False/None."""
    return _convert("html", html, output_path, options, token=token, client=client, max_total_charge_usd=max_total_charge_usd)


def from_url(url, output_path=False, options=None, *, token=None, client=None, max_total_charge_usd=1.00):
    """Render one public http(s) URL using the same options and charge cap."""
    return _convert("url", url, output_path, options, token=token, client=client, max_total_charge_usd=max_total_charge_usd)
