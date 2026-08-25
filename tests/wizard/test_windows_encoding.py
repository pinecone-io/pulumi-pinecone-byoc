import io

from wizard import _write_utf8


def test_a_legacy_codepage_stream_takes_the_check_mark():
    """cp1252 is what windows hands a process by default, and it has no U+2713."""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")

    _write_utf8(stream)
    stream.write("  [green]✓[/] Created Pulumi.yaml")
    stream.flush()

    assert "✓".encode() in raw.getvalue()


def test_a_stream_that_cannot_be_reconfigured_is_left_alone():
    """pytest and CI both replace stdout with objects that have no reconfigure()."""
    _write_utf8(io.StringIO())
