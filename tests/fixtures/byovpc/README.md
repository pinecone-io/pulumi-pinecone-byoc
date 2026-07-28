# byovpc fixture

Provisions a stand-in customer-managed AWS VPC so the BYO-VPC paths in
`pulumi_pinecone_byoc/aws/vpc.py` can be exercised against something that looks
like an enterprise landing zone.

## Shapes

The shape comes from the stack name — any stack containing one of these tokens
picks that mode, so `ilia-carve`, `public-ci` and `carve` all work. Set
`byovpc:mode` to override.

| mode     | creates                                                                | exercises                                          |
| -------- | ---------------------------------------------------------------------- | -------------------------------------------------- |
| `carve`  | no workload subnets                                                    | the module creates its own subnets in their VPC     |
| `public` | public + private subnets, role-tagged (built by the module's own `VPC`) | the module adopts subnets the customer already has |

NAT egress always sits on the VPC **main route table**, so subnets with no route
table of their own still reach the internet.

## Use

```bash
export AWS_PROFILE=byoc-dev
pulumi stack init ilia-public
pulumi config set aws:region us-east-2
pulumi config set --path byovpc:azs[0] us-east-2a
pulumi config set --path byovpc:azs[1] us-east-2b
pulumi install && pulumi up
```

Feed the result straight into the wizard:

```bash
eval "$(pulumi stack output wizard_env)"
cd ../../.. && uv run --with rich --with pyyaml python setup/wizard.py \
  --cloud aws --headless --output-dir ./ilia-byoc
```

Tear down the BYOC stack **before** this one, or the VPC delete will fail.

## Integration tests

`tests/test_byovpc_integration.py` drives the above and asserts the result
against real AWS. Stacks are named `$USER-<mode>`; profile, region and AZs come
from `pytest.ini`.

```bash
uv run --extra aws pytest -m integration tests/test_byovpc_integration.py -k public -s
```

Add `--keep-vpc` to leave the VPC up after the assertions run.

## When you need this program

Only for shapes that need working egress - the full-cluster `e2e` tier, whose
nodes have to reach the registry. The `network` tier applies the VPC component
with `pulumi up --target` and never routes a packet, so it uses the bare
`customer_vpc` pytest fixture instead: one `create-vpc` call, no second stack.

## Use

```bash
export AWS_PROFILE=byoc-dev
pulumi stack init ilia-carve
pulumi config set aws:region us-east-2
pulumi config set --path byovpc:azs[0] us-east-2a
pulumi config set --path byovpc:azs[1] us-east-2b
pulumi install && pulumi up
```

Feed the result straight into the wizard:

```bash
eval "$(pulumi stack output wizard_env)"
cd ../../.. && uv run --with rich --with pyyaml python setup/wizard.py \
  --cloud aws --headless --output-dir ./ilia-byoc
```

Tear down the BYOC stack **before** this one, or the VPC delete will fail.

## Integration tests

`tests/test_byovpc_integration.py` drives the above and asserts the result
against real AWS. Stacks are named `$USER-<mode>`; profile, region and AZs come
from `pytest.ini`.

```bash
uv run --extra aws pytest -m integration tests/test_byovpc_integration.py -k carve   -s
```

Add `--keep-vpc` to leave the VPC up after the assertions run.
