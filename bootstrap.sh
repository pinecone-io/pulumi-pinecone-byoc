#!/bin/bash
#
# Pinecone BYOC Bootstrap Script
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/pinecone-io/pulumi-pinecone-byoc/main/bootstrap.sh | bash
#
# With cloud pre-selected:
#   curl -fsSL https://raw.githubusercontent.com/pinecone-io/pulumi-pinecone-byoc/main/bootstrap.sh | bash -s -- --cloud aws
#   curl -fsSL https://raw.githubusercontent.com/pinecone-io/pulumi-pinecone-byoc/main/bootstrap.sh | bash -s -- --cloud gcp
#   curl -fsSL https://raw.githubusercontent.com/pinecone-io/pulumi-pinecone-byoc/main/bootstrap.sh | bash -s -- --cloud azure
#
set -e

BLUE='\033[0;34m'
GREEN='\033[0;32m'
RED='\033[0;31m'
DIM='\033[2m'
RESET='\033[0m'

CLOUD=""
REPO_BASE="https://raw.githubusercontent.com/pinecone-io/pulumi-pinecone-byoc/main"

# parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --cloud)
            CLOUD="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

echo ""
echo -e "${BLUE}Pinecone BYOC Setup${RESET}"
echo ""

# resolve SCRIPT_DIR before anything else (for local repo usage)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || SCRIPT_DIR=""

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
check_command "pulumi" "Pulumi CLI" "https://www.pulumi.com/docs/install/" || missing=1
check_command "kubectl" "kubectl" "https://kubernetes.io/docs/tasks/tools/" || missing=1

# cloud-specific tool checks
if [ "$CLOUD" = "aws" ]; then
    check_command "aws" "AWS CLI" "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html" || missing=1
elif [ "$CLOUD" = "gcp" ]; then
    check_command "gcloud" "Google Cloud SDK" "https://cloud.google.com/sdk/docs/install" || missing=1
elif [ "$CLOUD" = "azure" ]; then
    check_command "az" "Azure CLI" "https://learn.microsoft.com/en-us/cli/azure/install-azure-cli" || missing=1
else
    # no cloud pre-selected: require at least one cloud CLI
    has_cloud=0
    check_command "aws" "AWS CLI" "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html" && has_cloud=1
    check_command "gcloud" "Google Cloud SDK" "https://cloud.google.com/sdk/docs/install" && has_cloud=1
    check_command "az" "Azure CLI" "https://learn.microsoft.com/en-us/cli/azure/install-azure-cli" && has_cloud=1
    if [ $has_cloud -eq 0 ]; then
        missing=1
    fi
fi

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

# cloud-specific credential checks
if [ "$CLOUD" = "aws" ] || [ -z "$CLOUD" ]; then
    if command -v aws &> /dev/null; then
        echo "Checking AWS credentials..."
        if aws sts get-caller-identity &> /dev/null; then
            echo -e "  ${GREEN}✓${RESET} AWS credentials configured"
        else
            echo -e "  ${DIM}AWS credentials not configured (needed for AWS deployments)${RESET}"
        fi
    fi
fi
if [ "$CLOUD" = "gcp" ] || [ -z "$CLOUD" ]; then
    if command -v gcloud &> /dev/null; then
        echo "Checking GCP credentials..."
        if gcloud auth print-access-token &> /dev/null 2>&1; then
            echo -e "  ${GREEN}✓${RESET} GCP credentials configured"
        else
            echo -e "  ${DIM}GCP credentials not configured (needed for GCP deployments)${RESET}"
        fi
    fi
fi
if [ "$CLOUD" = "azure" ] || [ -z "$CLOUD" ]; then
    if command -v az &> /dev/null; then
        echo "Checking Azure credentials..."
        if az account show &> /dev/null 2>&1; then
            echo -e "  ${GREEN}✓${RESET} Azure credentials configured"
        else
            echo -e "  ${DIM}Azure credentials not configured (needed for Azure deployments)${RESET}"
        fi
    fi
fi

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

# copy wizard file from local repo or curl from GitHub
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/setup/wizard.py" ]; then
    cp "$SCRIPT_DIR/setup/wizard.py" wizard.py
else
    curl -fsSL "${REPO_BASE}/setup/wizard.py" -o wizard.py
fi

# create a temp pyproject.toml for the setup wizard dependencies
# (wizard.py will overwrite this with the actual project pyproject.toml)
# only install cloud-specific SDK when cloud is pre-selected
if [ "$CLOUD" = "aws" ]; then
    CLOUD_DEPS='    "boto3>=1.42.0",'
elif [ "$CLOUD" = "gcp" ]; then
    CLOUD_DEPS='    "google-auth>=2.0.0",'
elif [ "$CLOUD" = "azure" ]; then
    CLOUD_DEPS=''
else
    CLOUD_DEPS='    "boto3>=1.42.0",
    "google-auth>=2.0.0",'
fi

cat > pyproject.toml << EOF
[project]
name = "pinecone-byoc-setup"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pyyaml>=6.0",
    "rich>=14.0.0",
$CLOUD_DEPS
]
EOF

echo ""
echo "Running setup wizard..."
echo ""

# run the wizard (generates __main__.py and pyproject.toml for pulumi)
if [ -n "$CLOUD" ]; then
    uv run python wizard.py --cloud "$CLOUD"
else
    uv run python wizard.py
fi

# cleanup wizard setup file (keep .venv, pyproject.toml, uv.lock created by wizard)
rm -f wizard.py
