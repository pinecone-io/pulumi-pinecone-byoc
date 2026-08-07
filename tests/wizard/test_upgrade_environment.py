from wizard import _upgrade_environment

PROJECT = "pc"


def env_for(config):
    return _upgrade_environment(PROJECT, config)


def test_reads_the_inputs_the_generator_needs_from_stack_config():
    env = env_for(
        {
            "aws:region": "us-east-2",
            "pc:region": "us-east-2",
            "pc:vpc-cidr": "10.7.0.0/16",
            "pc:availability-zones": ["us-east-2a", "us-east-2b"],
            "pc:existing-vpc-id": "vpc-0abc",
            "pc:public-access-enabled": False,
            "pc:deletion-protection": True,
        }
    )
    assert env["PINECONE_REGION"] == "us-east-2"
    assert env["PINECONE_VPC_CIDR"] == "10.7.0.0/16"
    assert env["PINECONE_AZS"] == "us-east-2a,us-east-2b"
    assert env["PINECONE_EXISTING_VPC_ID"] == "vpc-0abc"
    assert env["PINECONE_PUBLIC_ACCESS"] == "false"
    assert env["PINECONE_DELETION_PROTECTION"] == "true"


def test_route_tables_round_trip_as_the_headless_form():
    env = env_for({"pc:existing-route-table-ids": {"us-east-2a": "rtb-1", "us-east-2b": "rtb-2"}})
    assert env["PINECONE_ROUTE_TABLE_IDS"] == "us-east-2a=rtb-1,us-east-2b=rtb-2"


def test_the_provider_block_is_left_to_the_generator():
    assert "PINECONE_REGION" not in env_for({"aws:region": "us-east-2"})


def test_secrets_and_tags_are_never_read_back_as_inputs():
    env = env_for(
        {
            "pc:pinecone-api-key": {"secure": "v1:cipher=="},
            "pc:tags": {"owner": "ilia"},
        }
    )
    assert env == {}


def test_the_control_plane_carries_over():
    env = env_for({"pc:global-env": "ci", "pc:api-url": "https://api-staging.pinecone.io"})
    assert env["PINECONE_GLOBAL_ENV"] == "ci"
    assert env["PINECONE_API_URL"] == "https://api-staging.pinecone.io"


def test_an_empty_stack_asks_for_nothing():
    assert env_for({}) == {}


def test_a_clone_url_with_a_token_in_it_is_never_written_into_a_project():
    from wizard import _https_remote

    assert _https_remote("git@github.com:pinecone-io/pulumi-pinecone-byoc.git") == (
        "https://github.com/pinecone-io/pulumi-pinecone-byoc"
    )
    assert _https_remote("ssh://git@github.com/pinecone-io/pulumi-pinecone-byoc.git") == (
        "https://github.com/pinecone-io/pulumi-pinecone-byoc"
    )
    assert _https_remote("https://github.com/pinecone-io/pulumi-pinecone-byoc") == (
        "https://github.com/pinecone-io/pulumi-pinecone-byoc"
    )
    for credentialed in (
        "https://x-access-token:ghp_secretvalue@github.com/pinecone-io/pulumi-pinecone-byoc.git",
        "https://ilia:hunter2@github.com/pinecone-io/pulumi-pinecone-byoc",
    ):
        stripped = _https_remote(credentialed)
        assert stripped == "https://github.com/pinecone-io/pulumi-pinecone-byoc"
        assert "@" not in stripped
        assert "secretvalue" not in stripped and "hunter2" not in stripped


def test_the_pinned_rev_is_read_back_out_of_a_generated_pyproject(tmp_path):
    from wizard import pinned_rev

    path = tmp_path / "pyproject.toml"
    path.write_text(
        '[project]\ndependencies = ["pulumi-pinecone-byoc[aws]"]\n\n'
        "[tool.uv.sources]\n"
        'pulumi-pinecone-byoc = { git = "https://github.com/pinecone-io/pulumi-pinecone-byoc",'
        ' rev = "94a9e90aa1b2c3d4e5f60718293a4b5c6d7e8f90" }\n'
    )
    assert pinned_rev(str(path)) == "94a9e90aa1b2c3d4e5f60718293a4b5c6d7e8f90"
    assert pinned_rev(str(tmp_path / "nothing-here.toml")) is None
