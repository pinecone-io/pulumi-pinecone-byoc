import pytest
from autocomplete import _CyclePrompt

OPTIONS = ["byoc-dev", "byoc-ci"]


def prompt(default=""):
    p = _CyclePrompt(OPTIONS, default)
    p.start()
    return p


def test_tab_from_nothing_selected_starts_at_the_first_option():
    p = prompt()
    p.tab(1)
    assert p.editor.value == "byoc-dev"


def test_shift_tab_from_nothing_selected_starts_at_the_last_entry():
    p = prompt()
    p.tab(-1)
    assert p.editor.value == ""
    p.tab(-1)
    assert p.editor.value == "byoc-ci"


def test_the_cycle_wraps_in_both_directions():
    p = prompt()
    seen = [(p.tab(1), p.editor.value)[1] for _ in range(4)]
    assert seen == ["byoc-dev", "byoc-ci", "", "byoc-dev"]

    p = prompt()
    seen = [(p.tab(-1), p.editor.value)[1] for _ in range(4)]
    assert seen == ["", "byoc-ci", "byoc-dev", ""]


def test_typing_replaces_a_cycled_value():
    p = prompt()
    p.tab(1)
    p.insert("x")
    assert p.editor.value == "x"


def test_typing_replaces_the_prefilled_default():
    p = prompt("byoc-ci")
    p.insert("x")
    assert p.editor.value == "x"


@pytest.mark.parametrize(
    ("name", "edit"),
    [
        ("backspace", lambda p: p.backspace()),
        ("cursor", lambda p: p.jump(0)),
    ],
)
def test_editing_a_cycled_value_keeps_it(name, edit):
    p = prompt()
    p.tab(1)
    edit(p)
    p.insert("!")
    assert p.editor.value != "!", f"{name} should end the suggestion, not wipe the value"


def test_a_default_is_offered_first_and_not_repeated():
    p = prompt("byoc-ci")
    assert p.editor.value == "byoc-ci"
    assert p.cycle == ["byoc-ci", "byoc-dev", ""]
