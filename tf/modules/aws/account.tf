data "aws_region" "current" {}

resource "terraform_data" "aws_account_ready" {
  input = {
    account_id = data.aws_caller_identity.current.account_id
    region     = data.aws_region.current.region
  }
}
