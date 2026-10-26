"""Trust-boundary validation of chat image attachments."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.main import MAX_CHAT_IMAGE_CHARS, _validate_chat_images

OK = "data:image/png;base64,iVBORw0KGgo="


def test_none_and_empty_are_fine():
    assert _validate_chat_images(None) == (None, None)
    assert _validate_chat_images([]) == (None, None)


def test_valid_images_pass():
    assert _validate_chat_images([OK, OK]) == ([OK, OK], None)


def test_rejects_too_many_wrong_type_and_too_large():
    assert _validate_chat_images([OK] * 4)[1]
    assert _validate_chat_images("notalist")[1]
    assert _validate_chat_images(["data:image/svg+xml;base64,PHN2Zz4="])[1]
    assert _validate_chat_images(["https://example.com/a.png"])[1]
    assert _validate_chat_images([OK + "\n<script>"])[1]
    big = "data:image/jpeg;base64," + "A" * MAX_CHAT_IMAGE_CHARS
    assert _validate_chat_images([big])[1]


def test_trailing_newline_rejected():
    assert _validate_chat_images([OK + "\n"])[1]

