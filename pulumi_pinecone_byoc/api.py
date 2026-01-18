import json
from typing import Tuple
from dataclasses import dataclass

from pydantic import BaseModel
import requests


class PineconeApiError(Exception):
    code: int
    msg: str

    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg

    def __str__(self):
        return f"PineconeApiError: {self.msg}"


class PineconeApiInternalError(Exception):
    pass


class CreateEnvironmentResponse(BaseModel):
    id: str
    name: str


class CreateServiceAccountResponse(BaseModel):
    id: str
    client_id: str
    client_secret: str


class ApiKey(BaseModel):
    id: str
    project_id: str


class CreateApiKeyResponse(BaseModel):
    key: ApiKey
    value: str


class CreateProjectResponse(BaseModel):
    id: str


@dataclass
class Auth0Config:
    domain: str
    client_id: str
    client_secret: str


def get_access_token(api_url: str, auth0: Auth0Config) -> str:
    url = f"{auth0.domain}/oauth/token"
    headers = {
        "Content-Type": "application/json",
        "cache-control": "no-cache",
    }
    data = {
        "client_id": auth0.client_id,
        "client_secret": auth0.client_secret,
        "audience": api_url + "/",
        "grant_type": "client_credentials",
    }
    response = requests.post(url, headers=headers, json=data)
    response.raise_for_status()
    return response.json().get("access_token")


def management_plane_url(api_url: str) -> str:
    return f"{api_url}/management"


def management_plane_headers(jwt: str) -> dict:
    return {
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
        "X-Pinecone-Api-Version": "unstable",
    }


def cpgw_url(api_url: str) -> str:
    return f"{api_url}/internal/cpgw/admin"


def cpgw_headers(pulumi_sa_secret) -> dict:
    return {
        "Api-Key": pulumi_sa_secret,
        "Content-Type": "application/json",
    }


def request(
    method: str,
    url: str,
    headers: dict = {},
    body: dict | None = None,
):
    response = requests.request(
        method=method,
        url=url,
        headers=headers,
        json=body,
    )

    try:
        message = response.json()
    except json.JSONDecodeError:
        message = response.text

    if not response.ok:
        error_msg = f"{response.status_code}: {message}"
        if 500 <= response.status_code < 600:
            raise PineconeApiInternalError(error_msg)
        else:
            raise PineconeApiError(response.status_code, error_msg)

    return message


def create_environment(
    cloud: str,
    region: str,
    global_env: str,
    org_id: str,
    api_url: str,
    secret: str,
) -> CreateEnvironmentResponse:
    body = {
        "organization_id": org_id,
        "cloud": cloud,
        "region": region,
        "global_env": global_env,
    }
    resp = request(
        "POST",
        f"{cpgw_url(api_url)}/environments",
        headers=cpgw_headers(secret),
        body=body,
    )

    try:
        environment = CreateEnvironmentResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiError(500, f"invalid response: {e}")

    return environment


def delete_environment(
    env_id: str,
    org_id: str,
    api_url: str,
    secret: str,
):
    request(
        "DELETE",
        f"{cpgw_url(api_url)}/environments/{env_id}?org-id={org_id}",
        headers=cpgw_headers(secret),
    )


def create_service_account(
    name: str,
    org_id: str,
    api_url: str,
    secret: str,
) -> Tuple[str, str, str]:
    try:
        resp = request(
            "POST",
            f"{cpgw_url(api_url)}/service-accounts",
            headers=cpgw_headers(secret),
            body={
                "name": name,
                "org_id": org_id,
            },
        )

        service_account = CreateServiceAccountResponse.model_validate_json(
            json.dumps(resp)
        )
    except Exception as e:
        raise PineconeApiInternalError(f"invalid response: {e}")

    return service_account.id, service_account.client_id, service_account.client_secret


def delete_service_account(
    id: str,
    api_url: str,
    secret: str,
):
    try:
        request(
            "DELETE",
            f"{cpgw_url(api_url)}/service-accounts/{id}",
            headers=cpgw_headers(secret),
        )
    except Exception as e:
        raise PineconeApiInternalError(f"failed to delete service account: {e}")


def create_api_key(
    org_id: str,
    project_name: str,
    key_name: str,
    api_url: str,
    auth0: Auth0Config,
) -> CreateApiKeyResponse:
    resp = request(
        "POST",
        f"{management_plane_url(api_url)}/organizations/{org_id}/projects",
        headers=management_plane_headers(get_access_token(api_url, auth0)),
        body={
            "name": project_name,
        },
    )

    try:
        project = CreateProjectResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiInternalError(f"invalid response: {e}")

    resp = request(
        "POST",
        f"{management_plane_url(api_url)}/projects/{project.id}/api-keys",
        headers=management_plane_headers(get_access_token(api_url, auth0)),
        body={"name": key_name, "roles": ["ProjectEditor"]},
    )

    try:
        api_key = CreateApiKeyResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiInternalError(f"invalid response: {e}")

    return api_key


def delete_api_key(
    project_id: str,
    api_url: str,
    auth0: Auth0Config,
):
    request(
        "DELETE",
        f"{management_plane_url(api_url)}/projects/{project_id}",
        headers=management_plane_headers(get_access_token(api_url, auth0)),
    )


class CreateDnsDelegationResponse(BaseModel):
    change_id: str
    status: str
    fqdn: str


class DeleteDnsDelegationResponse(BaseModel):
    change_id: str
    status: str


def create_dns_delegation(
    organization_id: str,
    environment_name: str,
    subdomain: str,
    nameservers: list[str],
    api_url: str,
    secret: str,
) -> CreateDnsDelegationResponse:
    body = {
        "_organization_id": organization_id,
        "_environment_name": environment_name,
        "subdomain": subdomain,
        "nameservers": nameservers,
    }
    resp = request(
        "POST",
        f"{cpgw_url(api_url)}/dns-delegation",
        headers=cpgw_headers(secret),
        body=body,
    )

    try:
        result = CreateDnsDelegationResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiError(500, f"invalid response: {e}")

    return result


def delete_dns_delegation(
    organization_id: str,
    subdomain: str,
    nameservers: list[str],
    api_url: str,
    secret: str,
) -> DeleteDnsDelegationResponse:
    body = {
        "_organization_id": organization_id,
        "subdomain": subdomain,
        "nameservers": nameservers,
    }
    resp = request(
        "POST",
        f"{cpgw_url(api_url)}/dns-delegation/delete",
        headers=cpgw_headers(secret),
        body=body,
    )

    try:
        result = DeleteDnsDelegationResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiError(500, f"invalid response: {e}")

    return result


class CreateDatadogApiKeyResponse(BaseModel):
    api_key: str
    key_id: str


class DeleteDatadogApiKeyResponse(BaseModel):
    deleted: bool


def create_datadog_api_key(
    organization_id: str,
    environment_name: str,
    api_url: str,
    secret: str,
) -> CreateDatadogApiKeyResponse:
    body = {
        "_organization_id": organization_id,
        "_environment_name": environment_name,
    }
    resp = request(
        "POST",
        f"{cpgw_url(api_url)}/datadog-credentials",
        headers=cpgw_headers(secret),
        body=body,
    )

    try:
        result = CreateDatadogApiKeyResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiError(500, f"invalid response: {e}")

    return result


def delete_datadog_api_key(
    organization_id: str,
    key_id: str,
    api_url: str,
    secret: str,
) -> DeleteDatadogApiKeyResponse:
    body = {
        "_organization_id": organization_id,
        "key_id": key_id,
    }
    resp = request(
        "POST",
        f"{cpgw_url(api_url)}/datadog-credentials/delete",
        headers=cpgw_headers(secret),
        body=body,
    )

    try:
        result = DeleteDatadogApiKeyResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiError(500, f"invalid response: {e}")

    return result
