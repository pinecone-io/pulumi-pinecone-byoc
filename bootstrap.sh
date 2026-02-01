#!/bin/bash
#
# Pinecone BYOC Bootstrap Script
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/pinecone-io/pulumi-pinecone-byoc/main/bootstrap.sh | bash
#
set -e

BLUE='\033[0;34m'
GREEN='\033[0;32m'
RED='\033[0;31m'
DIM='\033[2m'
RESET='\033[0m'

echo ""
echo -e "${BLUE}Pinecone BYOC Setup${RESET}"
echo ""

# check for required tools
check_command() {
    local cmd=$1
    local name=$2
    local install_url=$3

    if command -v "$cmd" &> /dev/null; then
        echo -e "  ${GREEN}✓${RESET} $name"
        return 0
    else
        echo -e "  ${RED}✗${RESET} $name ${DIM}(install: $install_url)${RESET}"
        return 1
    fi
}

echo "Checking requirements..."
echo ""

missing=0

check_command "python3" "Python 3.12+" "https://www.python.org/downloads/" || missing=1
check_command "uv" "uv" "https://docs.astral.sh/uv/getting-started/installation/" || missing=1
check_command "aws" "AWS CLI" "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html" || missing=1
check_command "pulumi" "Pulumi CLI" "https://www.pulumi.com/docs/install/" || missing=1
check_command "kubectl" "kubectl" "https://kubernetes.io/docs/tasks/tools/" || missing=1

echo ""

if [ $missing -eq 1 ]; then
    echo -e "${RED}Please install the missing tools above and try again.${RESET}"
    echo ""
    exit 1
fi

# check python version
python_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
required_version="3.12"

if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" != "$required_version" ]; then
    echo -e "${RED}Python $required_version or higher is required (found $python_version)${RESET}"
    exit 1
fi

# check aws credentials
echo "Checking AWS credentials..."
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "  ${RED}✗${RESET} AWS credentials not configured"
    echo ""
    echo -e "${DIM}Configure with: aws configure${RESET}"
    echo -e "${DIM}Or set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY${RESET}"
    exit 1
fi
echo -e "  ${GREEN}✓${RESET} AWS credentials configured"
echo ""

# get project directory (read from /dev/tty for curl pipe compatibility)
default_dir="pinecone-byoc"
echo -n "Project directory [$default_dir]: "
read project_dir < /dev/tty
project_dir="${project_dir:-$default_dir}"

if [ -d "$project_dir" ]; then
    echo -e "${RED}Directory '$project_dir' already exists${RESET}"
    exit 1
fi

mkdir -p "$project_dir"
cd "$project_dir"

echo ""
echo "Downloading setup wizard..."

# download wizard.py
curl -fsSL "https://raw.githubusercontent.com/pinecone-io/pulumi-pinecone-byoc/main/setup/wizard.py" -o wizard.py

# create a temp pyproject.toml for the setup wizard dependencies
# (wizard.py will overwrite this with the actual project pyproject.toml)
cat > pyproject.toml << 'EOF'
[project]
name = "pinecone-byoc-setup"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "boto3>=1.42.0",
    "pyyaml>=6.0",
    "rich>=14.0.0",
]
EOF

echo ""
echo "Running setup wizard..."
echo ""

# run the wizard (generates __main__.py and pyproject.toml for pulumi)
uv run python wizard.py

# cleanup wizard setup files (keep .venv, pyproject.toml, uv.lock created by wizard)
rm -f wizard.py
