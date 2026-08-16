"""Tear down what the VPC suite built, for the run that was killed before it could.

The suite destroys its own stacks; a runner that is cancelled or times out never
gets there. This is the same teardown, reachable by name from the destroy marker,
so nothing outside Python has to know which stacks exist or where their program
lives. The shape that adopts a VPC goes first: its subnets are inside the other
one's. The stand-in VPC the full e2e deploys into is torn down beside the
cluster that sits in it, in tests/test_destroy_e2e.py.
"""

import logging
from pathlib import Path

import pytest
from e2e.stacks import destroy_stack, find_stack, stack_name

pytestmark = pytest.mark.destroy

PROGRAM_DIR = Path(__file__).resolve().parent / "program"

SHAPES = ["private", "existing", "module"]


@pytest.mark.parametrize("shape", SHAPES)
def test_the_vpc_suite_leaves_no_stack_behind(shape):
    stack = stack_name("network", shape)
    if find_stack(stack) is None:
        pytest.skip(f"no stack named {stack} in the organization, nothing to destroy")

    logging.info("destroying %s left behind in %s", stack, PROGRAM_DIR)
    destroy_stack(PROGRAM_DIR, stack)
