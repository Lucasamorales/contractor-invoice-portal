"""Receipt validation (R-WIZ-04, R-SEC-10).

Real image bytes, not fixtures on disk: the point of these tests is what the
first few bytes of a file actually say, so they have to be genuine.
"""

from __future__ import annotations

import io

import pytest

from modules.invoices.services.receipts import (
    MAX_BYTES,
    Kind,
    ReceiptRejected,
    object_name,
    pending_name,
    prepare,
    sniff,
)

pytest.importorskip("PIL", reason="Pillow not installed")


def make_jpeg(*, with_gps: bool = False, size: tuple[int, int] = (60, 40)) -> bytes:
    """A JPEG, optionally carrying the metadata a phone would attach."""
    from PIL import Image

    out = io.BytesIO()
    image = Image.new("RGB", size, (200, 120, 60))

    if with_gps:
        exif = Image.Exif()
        exif[0x010F] = "Apple"  # Make
        exif[0x0110] = "iPhone 15 Pro"  # Model
        exif[0x0132] = "2026:09:24 13:45:02"  # DateTime
        # GPS IFD: where the photo was taken.
        exif[0x8825] = {
            1: "N",
            2: (51.0, 30.0, 26.0),
            3: "W",
            4: (0.0, 7.0, 39.0),
        }
        image.save(out, format="JPEG", exif=exif)
    else:
        image.save(out, format="JPEG")
    return out.getvalue()


def make_png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGBA", (40, 30), (10, 20, 30, 255)).save(out, format="PNG")
    return out.getvalue()


def make_heic() -> bytes:
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    out = io.BytesIO()
    Image.new("RGB", (50, 50), (90, 140, 200)).save(out, format="HEIF")
    return out.getvalue()


PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


# --------------------------------------------------------------------------
# The type comes from the bytes, never the name (R-SEC-10)
# --------------------------------------------------------------------------


def test_each_accepted_type_is_recognised() -> None:
    assert sniff(make_jpeg()) is Kind.JPEG
    assert sniff(make_png()) is Kind.PNG
    assert sniff(make_heic()) is Kind.HEIC
    assert sniff(PDF) is Kind.PDF


def test_an_executable_called_photo_jpg_is_refused() -> None:
    """The whole reason R-SEC-10 says magic bytes rather than extension."""
    windows_exe = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00" + b"\x00" * 200
    assert sniff(windows_exe) is None
    with pytest.raises(ReceiptRejected, match="could not read"):
        prepare(windows_exe, filename="photo.jpg")


def test_html_pretending_to_be_an_image_is_refused() -> None:
    """An HTML file served inline would run script on our own origin."""
    html = b"<html><script>alert(1)</script></html>" + b" " * 100
    assert sniff(html) is None
    with pytest.raises(ReceiptRejected):
        prepare(html, filename="receipt.png")


@pytest.mark.parametrize(
    "payload",
    [b"", b"tiny", b"not a real file at all, just some text " * 5],
)
def test_junk_is_refused(payload: bytes) -> None:
    with pytest.raises(ReceiptRejected):
        prepare(payload)


def test_the_message_names_what_we_accept() -> None:
    with pytest.raises(ReceiptRejected) as caught:
        prepare(b"nonsense" * 20, filename="thing.txt")
    assert "JPEG, PNG, HEIC or PDF" in caught.value.message
    assert "thing.txt" in caught.value.message


# --------------------------------------------------------------------------
# Size (R-WIZ-04)
# --------------------------------------------------------------------------


def test_a_file_over_ten_megabytes_is_refused_helpfully() -> None:
    with pytest.raises(ReceiptRejected) as caught:
        prepare(b"\xff\xd8\xff" + b"\x00" * MAX_BYTES)
    assert "10MB" in caught.value.message
    assert "photo of the receipt" in caught.value.message


# --------------------------------------------------------------------------
# EXIF (R-SEC-10)
# --------------------------------------------------------------------------


def test_a_photo_comes_out_without_its_metadata() -> None:
    """A phone photo of a lunch receipt carries where and when it was taken.

    Robin needs the amount, not somebody's movements.
    """
    from PIL import Image

    original = make_jpeg(with_gps=True)

    # The fixture really does carry it, or the test below proves nothing.
    with Image.open(io.BytesIO(original)) as before:
        assert before.getexif(), "the fixture has no EXIF to strip"
        assert 0x8825 in before.getexif(), "the fixture has no GPS tag"

    receipt = prepare(original)
    assert receipt.exif_stripped is True

    with Image.open(io.BytesIO(receipt.content)) as after:
        exif = after.getexif()
        assert 0x8825 not in exif, "the GPS coordinates survived"
        assert 0x010F not in exif, "the camera make survived"
        assert 0x0132 not in exif, "the timestamp survived"

    # And the raw bytes carry no GPS marker either.
    assert b"GPSInfo" not in receipt.content
    assert b"iPhone" not in receipt.content


def test_the_image_is_still_the_image() -> None:
    """Stripping must not quietly mangle what Robin has to read."""
    from PIL import Image

    receipt = prepare(make_jpeg(size=(120, 80)))
    with Image.open(io.BytesIO(receipt.content)) as image:
        assert image.size == (120, 80)


def test_a_png_stays_a_png() -> None:
    receipt = prepare(make_png())
    assert receipt.kind is Kind.PNG
    assert receipt.extension == "png"
    assert receipt.converted is False
    assert sniff(receipt.content) is Kind.PNG


# --------------------------------------------------------------------------
# HEIC (converted, not refused)
# --------------------------------------------------------------------------


def test_an_iphone_photo_becomes_a_jpeg() -> None:
    """iPhones produce HEIC by default. Robin should not have to find
    something that opens it."""
    receipt = prepare(make_heic(), filename="IMG_4821.HEIC")

    assert receipt.converted is True
    assert receipt.kind is Kind.JPEG
    assert receipt.extension == "jpg"
    assert receipt.content_type == "image/jpeg"
    # Genuinely a JPEG now, by its own bytes.
    assert sniff(receipt.content) is Kind.JPEG


def test_the_converted_photo_is_readable() -> None:
    from PIL import Image

    receipt = prepare(make_heic())
    with Image.open(io.BytesIO(receipt.content)) as image:
        assert image.size == (50, 50)
        assert image.format == "JPEG"


# --------------------------------------------------------------------------
# PDFs are left alone
# --------------------------------------------------------------------------


def test_a_pdf_passes_through_untouched() -> None:
    """A PDF carries no EXIF, and rewriting one risks breaking a document that
    was already fine."""
    receipt = prepare(PDF, filename="ticket.pdf")
    assert receipt.content == PDF
    assert receipt.kind is Kind.PDF
    assert receipt.extension == "pdf"
    assert receipt.exif_stripped is False


# --------------------------------------------------------------------------
# Where they live (R-STO-02)
# --------------------------------------------------------------------------


def test_object_names() -> None:
    assert object_name("p1", "inv1", "r1", "jpg") == "receipts/p1/inv1/r1.jpg"
    assert pending_name("p1", "r1", "pdf") == "pending-receipts/p1/r1.pdf"


def test_pending_uploads_sit_outside_the_receipts_prefix() -> None:
    """The bucket expires pending-receipts/ after a week.

    GCS lifecycle prefixes are literal, so if pending files lived under
    receipts/{person}/pending/ the rule would have to match "receipts/" — and
    would delete every attached receipt in the bucket. Keeping them apart is
    what makes the rule safe.
    """
    pending = pending_name("p1", "r1", "jpg")
    attached = object_name("p1", "inv1", "r1", "jpg")

    assert pending.startswith("pending-receipts/")
    assert not pending.startswith("receipts/")
    assert attached.startswith("receipts/")
    assert not attached.startswith("pending-receipts/")
