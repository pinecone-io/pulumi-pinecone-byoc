# byovpc fixture

Provisions a stand-in customer-managed AWS VPC so the BYO-VPC paths in
`pulumi_pinecone_byoc/aws/vpc.py` can be exercised against something that looks
like an enterprise landing zone.

## Shapes

The shape comes from the stack name — any stack containing one of these tokens
picks that mode, so `ilia-public`, `public-ci` and `public` all work. Set
`byovpc:mode` to override.

| mode     | creates                                                                | exercises                                  |
| -------- | ---------------------------------------------------------------------- | ------------------------------------------ |
| `public` | public + private subnets, role-tagged (built by the module's own `VPC`) | adopt-existing-subnets, public or private  |
| `carve`  | no workload subnets                                                    | adopt-VPC + module carves its own subnets  |

NAT egress always sits on the VPC **main route table**, so the subnets the module
carves inherit egress without a route table of their own.

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
uv run --extra aws pytest -m integration tests/test_byovpc_integration.py -k carve  -s
```

Add `--keep-vpc` to leave the VPC up after the assertions run.
