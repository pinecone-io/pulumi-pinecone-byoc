import json
import time
from typing import Tuple
from dataclasses import dataclass

import pulumi
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
    org_id: str
    org_name: str


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


def cpgw_infra_url(api_url: str) -> str:
    return f"{api_url}/internal/cpgw/infra"


def cpgw_bootstrap_url(api_url: str) -> str:
    # bootstrap routes use pinecone api key auth (not cpgw api key)
    return f"{api_url}/internal/cpgw/infra/bootstrap"


def cpgw_headers(pulumi_sa_secret) -> dict:
    return {
        "Api-Key": pulumi_sa_secret,
        "Content-Type": "application/json",
    }


def request(
    method: str,
    url: str,
    headers: dict | None = None,
    body: dict | None = None,
    max_retries: int = 3,
    base_delay: float = 2.0,
):
    last_error = None
    for attempt in range(max_retries + 1):
        response = requests.request(
            method=method,
            url=url,
            headers=headers or {},
            json=body,
        )

        try:
            message = response.json()
        except json.JSONDecodeError:
            message = response.text

        if response.ok:
            return message

        error_msg = f"{response.status_code}: {message}"

        # retry on 5xx errors
        if 500 <= response.status_code < 600:
            last_error = PineconeApiInternalError(error_msg)
            if attempt < max_retries:
                delay = base_delay * (2**attempt)
                pulumi.log.warn(
                    f"request failed ({error_msg}), retrying in {delay}s..."
                )
                time.sleep(delay)
                continue
            raise last_error
        else:
            raise PineconeApiError(response.status_code, error_msg)

    if last_error:
        raise last_error


def create_environment(
    cloud: str,
    region: str,
    global_env: str,
    api_url: str,
    secret: str,
) -> CreateEnvironmentResponse:
    body = {
        "cloud": cloud,
        "region": region,
        "global_env": global_env,
    }
    resp = request(
        "POST",
        f"{cpgw_bootstrap_url(api_url)}/environments",
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
    api_url: str,
    secret: str,
):
    request(
        "DELETE",
        f"{cpgw_bootstrap_url(api_url)}/environments/{env_id}",
        headers=cpgw_headers(secret),
    )


def create_service_account(
    name: str,
    api_url: str,
    secret: str,
) -> Tuple[str, str, str]:
    try:
        resp = request(
            "POST",
            f"{cpgw_infra_url(api_url)}/service-accounts",
            headers=cpgw_headers(secret),
            body={
                "name": name,
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
            f"{cpgw_infra_url(api_url)}/service-accounts/{id}",
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

    # if this fails, clean up the project to avoid orphans
    try:
        resp = request(
            "POST",
            f"{management_plane_url(api_url)}/projects/{project.id}/api-keys",
            headers=management_plane_headers(get_access_token(api_url, auth0)),
            body={"name": key_name, "roles": ["ProjectEditor"]},
        )
        api_key = CreateApiKeyResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        try:
            request(
                "DELETE",
                f"{management_plane_url(api_url)}/projects/{project.id}",
                headers=management_plane_headers(get_access_token(api_url, auth0)),
            )
        except Exception:
            pass  # best effort cleanup
        raise PineconeApiInternalError(f"failed to create api key: {e}")

    return api_key


def index_headers_api_key(api_key: str) -> dict:
    """headers for index operations using api key auth"""
    return {
        "Api-Key": api_key,
        "Content-Type": "application/json",
        "X-Pinecone-Api-Version": "2025-01",
    }


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


class CreateCpgwApiKeyResponse(BaseModel):
    id: str
    environment: str
    key: str


def create_cpgw_api_key(
    environment: str,
    api_url: str,
    pinecone_api_key: str,
) -> CreateCpgwApiKeyResponse:
    body = {
        "environment": environment,
    }
    resp = request(
        "POST",
        f"{cpgw_bootstrap_url(api_url)}/cpgw-api-keys",
        headers=cpgw_headers(pinecone_api_key),
        body=body,
    )

    try:
        result = CreateCpgwApiKeyResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiError(500, f"invalid response: {e}")

    return result


def delete_cpgw_api_key(
    key_id: str,
    api_url: str,
    pinecone_api_key: str,
):
    request(
        "DELETE",
        f"{cpgw_bootstrap_url(api_url)}/cpgw-api-keys/{key_id}",
        headers=cpgw_headers(pinecone_api_key),
    )


class CreateDnsDelegationResponse(BaseModel):
    change_id: str
    status: str
    fqdn: str


class DeleteDnsDelegationResponse(BaseModel):
    change_id: str
    status: str


def create_dns_delegation(
    subdomain: str,
    nameservers: list[str],
    api_url: str,
    cpgw_api_key: str,
) -> CreateDnsDelegationResponse:
    body = {
        "subdomain": subdomain,
        "nameservers": nameservers,
    }
    resp = request(
        "POST",
        f"{cpgw_infra_url(api_url)}/dns-delegation",
        headers=cpgw_headers(cpgw_api_key),
        body=body,
    )

    try:
        result = CreateDnsDelegationResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiError(500, f"invalid response: {e}")

    return result


def delete_dns_delegation(
    subdomain: str,
    nameservers: list[str],
    api_url: str,
    cpgw_api_key: str,
) -> DeleteDnsDelegationResponse:
    body = {
        "subdomain": subdomain,
        "nameservers": nameservers,
    }
    resp = request(
        "POST",
        f"{cpgw_infra_url(api_url)}/dns-delegation/delete",
        headers=cpgw_headers(cpgw_api_key),
        body=body,
    )

    try:
        result = DeleteDnsDelegationResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiError(500, f"invalid response: {e}")

    return result


class CreateAmpAccessResponse(BaseModel):
    pinecone_role_arn: str
    amp_remote_write_endpoint: str
    amp_region: str


class DeleteAmpAccessResponse(BaseModel):
    deleted: bool


def create_amp_access(
    workload_role_arn: str,
    api_url: str,
    cpgw_api_key: str,
) -> CreateAmpAccessResponse:
    body = {
        "workload_role_arn": workload_role_arn,
    }
    resp = request(
        "POST",
        f"{cpgw_infra_url(api_url)}/amp-access",
        headers=cpgw_headers(cpgw_api_key),
        body=body,
    )

    try:
        return CreateAmpAccessResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiError(500, f"invalid response: {e}")


def delete_amp_access(
    api_url: str,
    cpgw_api_key: str,
) -> DeleteAmpAccessResponse:
    resp = request(
        "POST",
        f"{cpgw_infra_url(api_url)}/amp-access/delete",
        headers=cpgw_headers(cpgw_api_key),
    )

    try:
        return DeleteAmpAccessResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiError(500, f"invalid response: {e}")


class CreateDatadogApiKeyResponse(BaseModel):
    api_key: str
    key_id: str


class DeleteDatadogApiKeyResponse(BaseModel):
    deleted: bool


def create_datadog_api_key(
    api_url: str,
    cpgw_api_key: str,
) -> CreateDatadogApiKeyResponse:
    # no body needed - org/env derived from cpgw api key auth context
    resp = request(
        "POST",
        f"{cpgw_infra_url(api_url)}/datadog-credentials",
        headers=cpgw_headers(cpgw_api_key),
    )

    try:
        result = CreateDatadogApiKeyResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiError(500, f"invalid response: {e}")

    return result


def delete_datadog_api_key(
    key_id: str,
    api_url: str,
    cpgw_api_key: str,
) -> DeleteDatadogApiKeyResponse:
    body = {
        "key_id": key_id,
    }
    resp = request(
        "POST",
        f"{cpgw_infra_url(api_url)}/datadog-credentials/delete",
        headers=cpgw_headers(cpgw_api_key),
        body=body,
    )

    try:
        result = DeleteDatadogApiKeyResponse.model_validate_json(json.dumps(resp))
    except Exception as e:
        raise PineconeApiError(500, f"invalid response: {e}")

    return result
