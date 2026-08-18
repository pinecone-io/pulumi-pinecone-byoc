import pytest
from wizard import AWSSetupWizard, AzureSetupWizard, GCPSetupWizard


def test_the_deploy_command_carries_the_chosen_profile(tmp_path, capsys):
    wizard_ = AWSSetupWizard(non_interactive=True)
    wizard_.COMMAND_PREFIX = "AWS_PROFILE=byoc-dev "

    wizard_._print_success(str(tmp_path))

    assert f"AWS_PROFILE=byoc-dev pulumi -C {tmp_path.name} up" in capsys.readouterr().out


@pytest.mark.parametrize(
    "cls", [AWSSetupWizard, GCPSetupWizard, AzureSetupWizard], ids=lambda c: c.__name__
)
def test_no_prefix_without_one(cls, tmp_path, capsys):
    cls(non_interactive=True)._print_success(str(tmp_path))

    assert f"pulumi -C {tmp_path.name} up" in capsys.readouterr().out
