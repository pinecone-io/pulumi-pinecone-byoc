import pytest
from wizard import AWSSetupWizard

AZS = ["us-east-1a", "us-east-1b", "us-east-1c"]


@pytest.mark.parametrize(
    ("headroom", "short"),
    [(None, 0), (11, 0), (3, 0), (1, 2), (0, 3), (-2, 5)],
)
def test_short_is_what_a_new_vpc_lacks(headroom, short):
    assert AWSSetupWizard._eips_short(AZS, headroom) == short


@pytest.mark.parametrize(
    "wizard_",
    [AWSSetupWizard(non_interactive=True), AWSSetupWizard(destroy=True)],
    ids=["non-interactive", "destroy"],
)
def test_headroom_is_left_to_preflight_when_nobody_is_answering(wizard_, monkeypatch):
    monkeypatch.setattr(
        wizard_, "_fetch_eip_headroom", lambda region: pytest.fail("must not read the cloud")
    )

    assert wizard_._eip_headroom("us-east-1") is None


def test_an_unreadable_headroom_is_left_to_preflight(monkeypatch):
    wizard_ = AWSSetupWizard()

    def refused(region):
        raise PermissionError("ec2:DescribeAddresses")

    monkeypatch.setattr(wizard_, "_fetch_eip_headroom", refused)

    assert wizard_._eip_headroom("us-east-1") is None


def walk(monkeypatch, headroom, vpc_id):
    """Drive run() up to the question after the VPC, recording how far it got."""
    wizard_ = AWSSetupWizard()
    asked: list[str] = []

    def step(name, value=None):
        def answer(*_args, **_kwargs):
            asked.append(name)
            return value

        return answer

    def stop(*_args, **_kwargs):
        asked.append("after_vpc")
        raise StopIteration

    monkeypatch.setattr(wizard_, "_validated_api_key", step("api_key", "pcsk_fake"))
    monkeypatch.setattr(wizard_, "_validate_aws_creds", step("creds", True))
    monkeypatch.setattr(wizard_, "_get_region", step("region", "us-east-1"))
    monkeypatch.setattr(wizard_, "_fetch_eip_headroom", step("eips", headroom))
    monkeypatch.setattr(wizard_, "_get_azs", step("azs", AZS))
    monkeypatch.setattr(wizard_, "_get_custom_ami_id", step("ami"))
    monkeypatch.setattr(wizard_, "_get_kms_key_arn", step("kms"))
    monkeypatch.setattr(wizard_, "_get_existing_vpc", step("vpc", vpc_id))
    monkeypatch.setattr(wizard_, "_get_subnet_ids", stop)

    try:
        finished = wizard_.run()
    except StopIteration:
        finished = None
    return finished, asked


def test_quota_is_read_before_the_azs_are_asked(monkeypatch):
    _, asked = walk(monkeypatch, 11, vpc_id=None)

    assert asked.index("region") < asked.index("eips") < asked.index("azs")


def test_a_new_vpc_without_enough_eips_stops_at_the_vpc_question(monkeypatch, capsys):
    finished, asked = walk(monkeypatch, 1, vpc_id=None)

    assert finished is False
    assert asked[-1] == "vpc"
    out = capsys.readouterr().out
    assert out.index("1 Elastic IPs free") < out.index("Request 2 more")


def test_an_existing_vpc_does_not_need_eips(monkeypatch):
    _, asked = walk(monkeypatch, 0, vpc_id="vpc-theirs")

    assert asked[-1] == "after_vpc"


def test_enough_eips_carries_on_without_a_warning(monkeypatch, capsys):
    _, asked = walk(monkeypatch, 11, vpc_id=None)

    assert asked[-1] == "after_vpc"
    assert "Elastic IP" not in capsys.readouterr().out
