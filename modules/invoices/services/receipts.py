"""Receipt files: what we accept, and what we do to them (R-WIZ-04, R-SEC-10).

Bytes go from the browser straight to storage and never through Cloud Run
(R-WIZ-04), so this runs on the object *after* it has been uploaded, before it
is attached to anything. A file that fails here is deleted and the person is
told immediately — long before they submit, so a bad photo can never threaten
an invoice.

Three rules, all from R-SEC-10:

* **The magic bytes decide the type, not the filename.** ``invoice.pdf`` that
  begins ``MZ`` is a Windows executable whatever it is called.
* **EXIF is stripped.** A phone photo of a lunch receipt carries the GPS
  coordinates of the restaurant and the time it was taken. Robin needs the
  amount, not somebody's movements.
* **Nothing is ever rendered inline.** Enforced where receipts are served, not
  here, but it is the reason an HTML file must never pass as an image.

HEIC is converted to JPEG rather than refused. iPhones produce it by default,
and the alternative is sixteen people being told to go and re-save a photo.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024  # R-WIZ-04
MAX_PER_INVOICE = 10  # R-WIZ-04

# Re-encoded at this quality when converting or stripping. A receipt has to be
# readable, not archival.
JPEG_QUALITY = 85

# Uploads wait here until their invoice exists. Kept top-level so the
# bucket lifecycle rule can expire it without touching attached receipts.
PENDING_PREFIX = "pending-receipts/"


class Kind(Enum):
    """What we accept (R-WIZ-04)."""

    JPEG = "image/jpeg"
    PNG = "image/png"
    HEIC = "image/heic"
    PDF = "application/pdf"


class ReceiptRejected(Exception):
    """The file cannot be accepted, with a message safe to show the person."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


ACCEPTED = "JPEG, PNG, HEIC or PDF"


@dataclass(frozen=True, slots=True)
class Receipt:
    """A file that has been checked and, where needed, rewritten."""

    content: bytes
    kind: Kind
    extension: str
    converted: bool = False
    exif_stripped: bool = False

    @property
    def content_type(self) -> str:
        return self.kind.value


def sniff(data: bytes) -> Kind | None:
    """Identify a file by its leading bytes. ``None`` if we do not accept it.

    Deliberately a short allow-list rather than a library: we accept four
    types, and a permissive sniffer is a way to accidentally accept a fifth.
    """
    if len(data) < 12:
        return None

    if data[:3] == b"\xff\xd8\xff":
        return Kind.JPEG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return Kind.PNG
    if data[:5] == b"%PDF-":
        return Kind.PDF
    # ISO base media file: 'ftyp' at offset 4, then the brand.
    if data[4:8] == b"ftyp" and data[8:12] in (
        b"heic",
        b"heix",
        b"hevc",
        b"heim",
        b"heis",
        b"mif1",
        b"msf1",
    ):
        return Kind.HEIC
    return None


def _strip_image(data: bytes, *, to_jpeg: bool) -> tuple[bytes, str]:
    """Re-encode an image without its metadata.

    Opening and re-saving through Pillow drops EXIF, and along with it the GPS
    tag. It also means anything that is not really an image fails here rather
    than reaching Robin's mail client.
    """
    # Imported here so the rest of the application does not need Pillow to
    # import — the domain tests and the seed have no use for it.
    import pillow_heif
    from PIL import Image, ImageOps

    pillow_heif.register_heif_opener()

    try:
        with Image.open(io.BytesIO(data)) as image:
            # Orientation is applied before the metadata goes, or a photo taken
            # sideways would arrive sideways.
            image = ImageOps.exif_transpose(image)

            if to_jpeg:
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                out = io.BytesIO()
                image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
                return out.getvalue(), "jpg"

            out = io.BytesIO()
            image.save(out, format="PNG", optimize=True)
            return out.getvalue(), "png"
    except (OSError, ValueError) as exc:
        raise ReceiptRejected(
            "That image could not be read. Try saving it again, or use a PDF."
        ) from exc


def prepare(data: bytes, *, filename: str = "") -> Receipt:
    """Check a receipt and return the bytes to keep.

    Raises ``ReceiptRejected`` with something a person can act on. The filename
    is used only in messages; it never decides anything.
    """
    if not data:
        raise ReceiptRejected("That file is empty.")
    if len(data) > MAX_BYTES:
        megabytes = len(data) / 1024 / 1024
        raise ReceiptRejected(
            f"That file is {megabytes:.1f}MB and the limit is 10MB. "
            "A photo of the receipt is usually enough."
        )

    kind = sniff(data)
    if kind is None:
        named = f" ({filename})" if filename else ""
        raise ReceiptRejected(
            f"We could not read that file{named}. We accept {ACCEPTED}."
        )

    if kind is Kind.PDF:
        # Left as-is. A PDF carries no EXIF, and re-writing one risks breaking
        # a document that is already fine.
        return Receipt(content=data, kind=Kind.PDF, extension="pdf")

    if kind is Kind.HEIC:
        # Converted rather than refused: iPhones produce HEIC by default, and
        # Robin should not have to find something that opens it.
        content, extension = _strip_image(data, to_jpeg=True)
        return Receipt(
            content=content,
            kind=Kind.JPEG,
            extension=extension,
            converted=True,
            exif_stripped=True,
        )

    to_jpeg = kind is Kind.JPEG
    content, extension = _strip_image(data, to_jpeg=to_jpeg)
    return Receipt(
        content=content,
        kind=Kind.JPEG if to_jpeg else Kind.PNG,
        extension=extension,
        exif_stripped=True,
    )


def object_name(
    person_id: str, invoice_id: str, receipt_id: str, extension: str
) -> str:
    """R-STO-02: ``receipts/{person_id}/{invoice_id}/{uuid}.{ext}``."""
    return f"receipts/{person_id}/{invoice_id}/{receipt_id}.{extension}"


def pending_name(person_id: str, receipt_id: str, extension: str) -> str:
    """Where a receipt waits before its invoice exists.

    ``expenses.invoice_id`` is NOT NULL (R-DATA-05), so an expense — and its
    receipt — cannot be attached to anything until the invoice is created at
    submit. Uploads land here first and move on submit. A lifecycle rule sweeps
    anything left behind, so an abandoned wizard needs no cleanup job.

    A top-level prefix, not ``receipts/{person}/pending/``, and the reason
    matters: GCS lifecycle prefixes are literal with no wildcard, so a rule
    aimed at the nested form would have to match ``receipts/`` and would delete
    every attached receipt in the bucket after a week.
    """
    return f"{PENDING_PREFIX}{person_id}/{receipt_id}.{extension}"
