"""--keep-failed, asked by a fixture that outlives the test that failed.

The networks and clusters an e2e run builds are module-scoped, and a phase only
ever runs on a function - so where the report is recorded decides whether a failed
run leaves anything to look at.
"""

from types import SimpleNamespace

from e2e.settings import keep_stacks, remember_report


class Node:
    def __init__(self, above=()):
        self.above = list(above)

    def listchain(self):
        return [*self.above, self]


def asking(node, **options):
    return SimpleNamespace(
        node=node,
        config=SimpleNamespace(getoption=lambda name: options.get(name, False)),
    )


def a_module_and_its_test():
    module = Node()
    return module, Node(above=[module])


def test_a_failure_reaches_the_module_a_fixture_is_scoped_to():
    module, item = a_module_and_its_test()

    remember_report(item, SimpleNamespace(when="call", failed=True))

    assert keep_stacks(asking(module, **{"--keep-failed": True}))
    assert keep_stacks(asking(item, **{"--keep-failed": True}))


def test_a_run_that_passed_leaves_nothing_up():
    module, item = a_module_and_its_test()

    remember_report(item, SimpleNamespace(when="call", failed=False))

    assert not keep_stacks(asking(module, **{"--keep-failed": True}))


def test_a_setup_that_failed_counts_too():
    module, item = a_module_and_its_test()

    remember_report(item, SimpleNamespace(when="setup", failed=True))

    assert keep_stacks(asking(module, **{"--keep-failed": True}))


def test_without_the_flag_a_failure_still_tears_down():
    module, item = a_module_and_its_test()

    remember_report(item, SimpleNamespace(when="call", failed=True))

    assert not keep_stacks(asking(module))
