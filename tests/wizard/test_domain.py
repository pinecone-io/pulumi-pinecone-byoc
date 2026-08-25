import pathlib

import pytest
from wizard import (
    CERTIFICATE_NAME_MAX_LENGTH,
    PINECONE_HOSTED_DOMAIN,
    PRIVATE_CERTIFICATE_LABEL,
    AWSSetupWizard,
    AzureSetupWizard,
    BaseSetupWizard,
    GCPSetupWizard,
    NonInteractiveInputRequired,
)

from pulumi_pinecone_byoc.common import naming

REGIONS = ("us-east-1", "us-east-2", "ap-southeast-1")
ENVS = ("prod", "ci", "staging")


def test_the_wizard_and_the_module_agree_on_the_limit():
    assert CERTIFICATE_NAME_MAX_LENGTH == naming.CERTIFICATE_NAME_MAX_LENGTH
    assert PINECONE_HOSTED_DOMAIN == naming.PINECONE_HOSTED_DOMAIN
    assert PRIVATE_CERTIFICATE_LABEL == naming.PRIVATE_CERTIFICATE_LABEL


@pytest.mark.parametrize("global_env", ENVS)
@pytest.mark.parametrize("region", REGIONS)
def test_the_wizard_accepts_exactly_what_the_deploy_accepts(monkeypatch, region, global_env):
    monkeypatch.setenv("PINECONE_GLOBAL_ENV", global_env)
    budget = AWSSetupWizard(non_interactive=True)._domain_budget(region)

    naming.refuse_a_domain_no_certificate_can_cover("a" * budget, region, global_env)
    with pytest.raises(ValueError, match="characters"):
        naming.refuse_a_domain_no_certificate_can_cover("a" * (budget + 1), region, global_env)


def test_a_non_prod_cell_gets_less_room_than_a_prod_one(monkeypatch):
    monkeypatch.setenv("PINECONE_GLOBAL_ENV", "prod")
    prod = AWSSetupWizard(non_interactive=True)._domain_budget("us-east-2")
    monkeypatch.setenv("PINECONE_GLOBAL_ENV", "staging")
    staging = AWSSetupWizard(non_interactive=True)._domain_budget("us-east-2")
    assert prod - staging == len("staging-")


def test_an_unset_global_env_is_treated_as_prod(monkeypatch):
    monkeypatch.delenv("PINECONE_GLOBAL_ENV", raising=False)
    unset = AWSSetupWizard(non_interactive=True)._domain_budget("us-east-2")
    monkeypatch.setenv("PINECONE_GLOBAL_ENV", "prod")
    assert unset == AWSSetupWizard(non_interactive=True)._domain_budget("us-east-2")


@pytest.mark.parametrize(
    ("domain", "well_formed"),
    [
        ("corp.example.com", True),
        ("pc.acme.com", True),
        ("acme-1.com", True),
        ("acme", False),
        (".acme.com", False),
        ("acme.com.", False),
        ("Acme.com", False),
        ("acme.com/path", False),
        ("acme com", False),
    ],
)
def test_a_domain_that_is_not_a_domain_is_rejected(domain, well_formed):
    assert AWSSetupWizard._domain_is_well_formed(domain) is well_formed


def test_aws_asks_for_a_domain(monkeypatch):
    monkeypatch.setenv("PINECONE_DOMAIN", "corp.example.com")
    assert AWSSetupWizard(non_interactive=True)._get_domain("us-east-2") == "corp.example.com"


def test_a_blank_answer_leaves_the_cell_on_a_pinecone_domain(monkeypatch):
    monkeypatch.setenv("PINECONE_DOMAIN", "")
    assert AWSSetupWizard(non_interactive=True)._get_domain("us-east-2") is None


@pytest.mark.parametrize("wizard", (GCPSetupWizard, AzureSetupWizard), ids=lambda w: w.__name__)
def test_no_other_cloud_offers_a_domain_it_could_not_delegate(monkeypatch, wizard):
    monkeypatch.setenv("PINECONE_DOMAIN", "corp.example.com")
    assert not hasattr(wizard(non_interactive=True), "_get_domain")


@pytest.mark.parametrize("cloud", ("gcp", "azure"))
def test_a_domain_that_reached_another_cloud_anyway_is_refused(cloud):
    with pytest.raises(ValueError, match="resolves under pinecone.io"):
        naming.refuse_a_domain_only_aws_can_be_delegated("corp.example.com", cloud)


@pytest.mark.parametrize(
    "domain",
    ["pinecone.some-very-long-company-name-indeed.com", "not-a-domain"],
    ids=["too_long", "malformed"],
)
def test_a_domain_ci_cannot_use_is_refused_rather_than_asked_for_again(monkeypatch, domain):
    monkeypatch.setenv("PINECONE_GLOBAL_ENV", "ci")
    monkeypatch.setenv("PINECONE_DOMAIN", domain)

    with pytest.raises(NonInteractiveInputRequired) as raised:
        AWSSetupWizard(non_interactive=True)._get_domain("us-east-2")

    assert raised.value.env_var == "PINECONE_DOMAIN"


def asking(monkeypatch, answers, region="us-east-2"):
    wizard = AWSSetupWizard()
    monkeypatch.setattr(wizard, "_prompt", lambda *_args, **_kwargs: answers.pop(0))
    return wizard, wizard._get_domain(region)


def test_a_typo_is_asked_about_again_rather_than_ending_the_run(monkeypatch):
    _, domain = asking(monkeypatch, ["not-a-domain", "corp.example.com"])
    assert domain == "corp.example.com"


def test_a_re_ask_does_not_renumber_the_steps(monkeypatch):
    before = AWSSetupWizard()._current_step
    wizard, _ = asking(monkeypatch, ["not-a-domain", "pinecone.acmecorporation.com", "pc.acme.com"])
    assert wizard._current_step == before + 1


def test_the_step_total_covers_the_domain_question():
    assert AWSSetupWizard.TOTAL_STEPS > BaseSetupWizard.TOTAL_STEPS


def generated_programs():
    import ast

    source = pathlib.Path(__file__).resolve().parents[2] / "setup" / "wizard.py"
    return [
        node.value
        for node in ast.walk(ast.parse(source.read_text()))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "PineconeAWSClusterArgs(" in node.value
        or isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and ("PineconeGCPClusterArgs(" in node.value or "PineconeAzureClusterArgs(" in node.value)
    ]


def test_a_generated_program_imports_only_the_published_surface():
    programs = generated_programs()
    assert len(programs) == 3
    for program in programs:
        for line in program.splitlines():
            if line.startswith("from pulumi_pinecone_byoc"):
                module = line.split()[1]
                assert module in (
                    "pulumi_pinecone_byoc.aws",
                    "pulumi_pinecone_byoc.gcp",
                    "pulumi_pinecone_byoc.azure",
                ), f"{module} is not part of the published surface a customer's venv may have"


def test_a_two_label_domain_is_refused_without_inventing_one(monkeypatch, capsys):
    """Replacing the first label of acme.com proposes pc.com, which is nobody's zone here."""
    wizard = AWSSetupWizard(non_interactive=True)
    budget = wizard._domain_budget("ap-southeast-1")
    theirs = "c" * (budget + 4) + ".com"

    monkeypatch.setenv("PINECONE_DOMAIN", theirs)
    with pytest.raises(NonInteractiveInputRequired):
        wizard._get_domain("ap-southeast-1")

    assert "pc.com" not in capsys.readouterr().out
