"""Regression tests for env_positive_int — the config-default-0 trap.

Locks the fix for the 1.18.2 bug where MAX_ITERATIONS=0 (the add-on option
default, exported by run.sh) made the agent loop abort every turn with
"Maximum iteration limit reached".
"""
import os

import pytest

from env_utils import env_positive_int

KEY = "TEST_ENV_POSITIVE_INT"


@pytest.fixture(autouse=True)
def _clean_env():
    os.environ.pop(KEY, None)
    yield
    os.environ.pop(KEY, None)


def test_unset_uses_default():
    assert env_positive_int(KEY, 25) == 25


def test_zero_uses_default():
    # The actual 1.18.2 bug: option default 0, exported as "0".
    os.environ[KEY] = "0"
    assert env_positive_int(KEY, 25) == 25


def test_blank_uses_default():
    os.environ[KEY] = ""
    assert env_positive_int(KEY, 25) == 25


def test_whitespace_uses_default():
    os.environ[KEY] = "   "
    assert env_positive_int(KEY, 25) == 25


def test_non_numeric_uses_default():
    os.environ[KEY] = "abc"
    assert env_positive_int(KEY, 25) == 25


def test_negative_uses_default():
    # "-5".isdigit() is False, so the default applies.
    os.environ[KEY] = "-5"
    assert env_positive_int(KEY, 25) == 25


def test_positive_value_overrides():
    os.environ[KEY] = "40"
    assert env_positive_int(KEY, 25) == 40
