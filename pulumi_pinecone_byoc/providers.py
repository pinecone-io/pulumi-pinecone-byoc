"""
Pulumi Dynamic Providers for Pinecone resources.

These providers manage Pinecone control plane resources like environments,
service accounts, and API keys through the Pulumi resource lifecycle.
"""

import asyncio
from typing import Optional

import pulumi
from pulumi import Output
from pulumi.dynamic import (
    Resource,
    ResourceProvider,
    CreateResult,
    DiffResult,
    UpdateResult,
)

from .api import (
    create_environment,
    delete_environment,
    create_service_account,
    delete_service_account,
    create_api_key,
    delete_api_key,
    create_dns_delegation,
    delete_dns_delegation,
    create_datadog_api_key,
    delete_datadog_api_key,
)


# =============================================================================
# Environment Resource
# =============================================================================


class EnvironmentArgs:
    """Arguments for creating a Pinecone Environment."""

    cloud: str
    region: str
    global_env: str
    org_id: str
    api_url: str
    secret: str

    def __init__(
        self,
        cloud: str,
        region: str,
        global_env: str,
        org_id: str,
        api_url: str,
        secret: str,
    ):
        self.cloud = cloud
        self.region = region
        self.global_env = global_env
        self.org_id = org_id
        self.api_url = api_url
        self.secret = secret


class EnvironmentProvider(ResourceProvider):
    """Provider for managing Pinecone environments."""

    def create(self, props):
        cloud, region, global_env, org_id, api_url, secret = (
            props["cloud"],
            props["region"],
            props["global_env"],
            props["org_id"],
            props["api_url"],
            props["secret"],
        )
        environment = asyncio.run(
            asyncio.to_thread(
                create_environment,
                cloud=cloud,
                region=region,
                global_env=global_env,
                org_id=org_id,
                api_url=api_url,
                secret=secret,
            )
        )

        return CreateResult(environment.id, {**props, "env_name": environment.name})

    def diff(self, id, olds, news):
        # force replace if env_name is missing (state corruption)
        if not olds.get("env_name"):
            return DiffResult(
                changes=True, replaces=["env_name"], delete_before_replace=True
            )

        # cloud is case-insensitive, don't replace on case change
        old_cloud = olds.get("cloud", "").lower()
        new_cloud = news.get("cloud", "").lower()
        replaces = []
        if old_cloud != new_cloud:
            replaces.append("cloud")
        if olds.get("region") != news.get("region"):
            replaces.append("region")
        if olds.get("global_env") != news.get("global_env"):
            replaces.append("global_env")
        if olds.get("org_id") != news.get("org_id"):
            replaces.append("org_id")
        return DiffResult(
            changes=len(replaces) > 0 or olds.get("cloud") != news.get("cloud"),
            replaces=replaces,
            stables=["env_name"],
            delete_before_replace=True,
        )

    def update(self, id, olds, news):
        # env_name is stable, carry it forward
        env_name = olds.get("env_name")
        return UpdateResult({**news, "env_name": env_name})

    def delete(self, id, props):
        org_id = props.get("org_id")
        api_url = props.get("api_url")
        secret = props.get("secret")
        # skip delete if we don't have the required props (state corruption)
        if not all([org_id, api_url, secret]):
            return {}
        asyncio.run(
            asyncio.to_thread(
                delete_environment,
                env_id=id,
                org_id=org_id,
                api_url=api_url,
                secret=secret,
            )
        )
        return {}


class Environment(Resource):
    """
    A Pinecone Environment resource.

    Creates and manages a Pinecone environment in the control plane.
    """

    id: Output[str]
    env_name: Output[str]

    def __init__(
        self,
        name: str,
        args: EnvironmentArgs,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        full_args = {
            "id": None,
            "env_name": None,
            "cloud": args.cloud,
            "region": args.region,
            "global_env": args.global_env,
            "org_id": args.org_id,
            "api_url": args.api_url,
            "secret": args.secret,
        }
        super().__init__(
            EnvironmentProvider(),
            name,
            full_args,
            opts,
        )


# =============================================================================
# ServiceAccount Resource
# =============================================================================


class ServiceAccountArgs:
    """Arguments for creating a Pinecone Service Account."""

    name: str
    org_id: str
    api_url: str
    secret: str

    def __init__(
        self,
        name: str,
        org_id: str,
        api_url: str,
        secret: str,
    ):
        self.name = name
        self.org_id = org_id
        self.api_url = api_url
        self.secret = secret


class ServiceAccountProvider(ResourceProvider):
    """Provider for managing Pinecone service accounts."""

    def create(self, props):
        name, org_id, api_url, secret = (
            props["name"],
            props["org_id"],
            props["api_url"],
            props["secret"],
        )
        service_account_id, client_id, client_secret = asyncio.run(
            asyncio.to_thread(
                create_service_account,
                name=name,
                org_id=org_id,
                api_url=api_url,
                secret=secret,
            )
        )

        return CreateResult(
            service_account_id,
            {**props, "client_id": client_id, "client_secret": client_secret},
        )

    def diff(self, id, olds, news):
        # force replace if client_id or client_secret is missing (state corruption)
        if not olds.get("client_id") or not olds.get("client_secret"):
            return DiffResult(
                changes=True, replaces=["client_id"], delete_before_replace=True
            )
        # replace if name or org_id changes
        replaces = []
        if olds.get("name") != news.get("name"):
            replaces.append("name")
        if olds.get("org_id") != news.get("org_id"):
            replaces.append("org_id")
        return DiffResult(
            changes=len(replaces) > 0,
            replaces=replaces,
            stables=["client_id", "client_secret"],
            delete_before_replace=True,
        )

    def delete(self, id, props):
        # skip delete if missing required props (state corruption)
        api_url = props.get("api_url")
        secret = props.get("secret")
        if not all([api_url, secret]):
            return {}
        asyncio.run(
            asyncio.to_thread(
                delete_service_account,
                id=id,
                api_url=api_url,
                secret=secret,
            )
        )
        return {}


class ServiceAccount(Resource):
    """
    A Pinecone Service Account resource.

    Creates and manages a service account for programmatic access to Pinecone.
    """

    id: Output[str]
    client_id: Output[str]
    client_secret: Output[str]

    def __init__(
        self,
        name: str,
        args: ServiceAccountArgs,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        full_args = {
            "id": None,
            "client_id": None,
            "client_secret": None,
            "name": args.name,
            "org_id": args.org_id,
            "api_url": args.api_url,
            "secret": args.secret,
        }
        super().__init__(
            ServiceAccountProvider(),
            name,
            full_args,
            opts,
        )


# =============================================================================
# ApiKey Resource
# =============================================================================


class ApiKeyArgs:
    org_id: str
    project_name: str
    key_name: str
    api_url: str
    auth0_domain: str
    auth0_client_id: str
    auth0_client_secret: str

    def __init__(
        self,
        org_id: str,
        project_name: str,
        key_name: str,
        api_url: str,
        auth0_domain: str,
        auth0_client_id: str,
        auth0_client_secret: str,
    ):
        self.org_id = org_id
        self.project_name = project_name
        self.key_name = key_name
        self.api_url = api_url
        self.auth0_domain = auth0_domain
        self.auth0_client_id = auth0_client_id
        self.auth0_client_secret = auth0_client_secret


class ApiKeyProvider(ResourceProvider):
    def create(self, props):
        from .api import Auth0Config

        auth0 = Auth0Config(
            domain=props["auth0_domain"],
            client_id=props["auth0_client_id"],
            client_secret=props["auth0_client_secret"],
        )
        api_key_response = asyncio.run(
            asyncio.to_thread(
                create_api_key,
                org_id=props["org_id"],
                project_name=props["project_name"],
                key_name=props["key_name"],
                api_url=props["api_url"],
                auth0=auth0,
            )
        )

        return CreateResult(
            api_key_response.key.project_id,
            {
                **props,
                "api_key_id": api_key_response.key.id,
                "value": api_key_response.value,
                "project_id": api_key_response.key.project_id,
            },
        )

    def diff(self, id, olds, news):
        # force replace if value is missing (state corruption / key not recoverable)
        if not olds.get("value"):
            return DiffResult(
                changes=True, replaces=["value"], delete_before_replace=True
            )

        # replace if project_name or key_name changes
        replaces = []
        if olds.get("project_name") != news.get("project_name"):
            replaces.append("project_name")
        if olds.get("key_name") != news.get("key_name"):
            replaces.append("key_name")
        if olds.get("org_id") != news.get("org_id"):
            replaces.append("org_id")
        return DiffResult(
            changes=len(replaces) > 0,
            replaces=replaces,
            stables=["value", "api_key_id", "project_id"],
            delete_before_replace=True,
        )

    def delete(self, id, props):
        from .api import Auth0Config

        # skip delete if missing required auth0 props (state corruption)
        auth0_domain = props.get("auth0_domain")
        auth0_client_id = props.get("auth0_client_id")
        auth0_client_secret = props.get("auth0_client_secret")
        api_url = props.get("api_url")
        if not all([auth0_domain, auth0_client_id, auth0_client_secret, api_url]):
            return {}

        auth0 = Auth0Config(
            domain=auth0_domain,
            client_id=auth0_client_id,
            client_secret=auth0_client_secret,
        )
        asyncio.run(
            asyncio.to_thread(
                delete_api_key,
                project_id=id,
                api_url=api_url,
                auth0=auth0,
            )
        )
        return {}


class ApiKey(Resource):
    id: Output[str]
    api_key_id: Output[str]
    value: Output[str]
    project_id: Output[str]

    def __init__(
        self,
        name: str,
        args: ApiKeyArgs,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        full_args = {
            "id": None,
            "api_key_id": None,
            "value": None,
            "project_id": None,
            "org_id": args.org_id,
            "project_name": args.project_name,
            "key_name": args.key_name,
            "api_url": args.api_url,
            "auth0_domain": args.auth0_domain,
            "auth0_client_id": args.auth0_client_id,
            "auth0_client_secret": args.auth0_client_secret,
        }
        super().__init__(
            ApiKeyProvider(),
            name,
            full_args,
            opts,
        )


class DnsDelegationArgs:
    organization_id: str
    environment_name: str
    subdomain: str
    nameservers: list[str]
    api_url: str
    secret: str

    def __init__(
        self,
        organization_id: str,
        environment_name: str,
        subdomain: str,
        nameservers: list[str],
        api_url: str,
        secret: str,
    ):
        self.organization_id = organization_id
        self.environment_name = environment_name
        self.subdomain = subdomain
        self.nameservers = nameservers
        self.api_url = api_url
        self.secret = secret


class DnsDelegationProvider(ResourceProvider):
    def create(self, props):
        result = asyncio.run(
            asyncio.to_thread(
                create_dns_delegation,
                organization_id=props["organization_id"],
                environment_name=props["environment_name"],
                subdomain=props["subdomain"],
                nameservers=props["nameservers"],
                api_url=props["api_url"],
                secret=props["secret"],
            )
        )

        return CreateResult(
            result.fqdn,
            {**props, "fqdn": result.fqdn, "change_id": result.change_id},
        )

    def diff(self, id, olds, news):
        # force replace if subdomain or environment_name changes
        replaces = []
        if olds.get("subdomain") != news.get("subdomain"):
            replaces.append("subdomain")
        if olds.get("environment_name") != news.get("environment_name"):
            replaces.append("environment_name")
        changes = len(replaces) > 0 or olds.get("nameservers") != news.get(
            "nameservers"
        )
        return DiffResult(
            changes=changes, replaces=replaces, delete_before_replace=True
        )

    def update(self, id, olds, news):
        # re-create delegation with new nameservers
        result = asyncio.run(
            asyncio.to_thread(
                create_dns_delegation,
                organization_id=news["organization_id"],
                environment_name=news["environment_name"],
                subdomain=news["subdomain"],
                nameservers=news["nameservers"],
                api_url=news["api_url"],
                secret=news["secret"],
            )
        )
        return UpdateResult(
            {**news, "fqdn": result.fqdn, "change_id": result.change_id}
        )

    def delete(self, id, props):
        org_id = props.get("organization_id")
        subdomain = props.get("subdomain")
        nameservers = props.get("nameservers")
        api_url = props.get("api_url")
        secret = props.get("secret")
        # skip delete if we don't have the required props (state corruption)
        if not all([org_id, subdomain, nameservers, api_url, secret]):
            return {}
        asyncio.run(
            asyncio.to_thread(
                delete_dns_delegation,
                organization_id=org_id,
                subdomain=subdomain,
                nameservers=nameservers,
                api_url=api_url,
                secret=secret,
            )
        )
        return {}


class DnsDelegation(Resource):
    id: Output[str]
    fqdn: Output[str]
    change_id: Output[str]

    def __init__(
        self,
        name: str,
        args: DnsDelegationArgs,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        full_args = {
            "id": None,
            "fqdn": None,
            "change_id": None,
            "organization_id": args.organization_id,
            "environment_name": args.environment_name,
            "subdomain": args.subdomain,
            "nameservers": args.nameservers,
            "api_url": args.api_url,
            "secret": args.secret,
        }
        super().__init__(
            DnsDelegationProvider(),
            name,
            full_args,
            opts,
        )


# =============================================================================
# DatadogApiKey Resource
# =============================================================================


class DatadogApiKeyArgs:
    """Arguments for creating a Datadog API Key via cpgw."""

    organization_id: str
    environment_name: str
    api_url: str
    secret: str

    def __init__(
        self,
        organization_id: str,
        environment_name: str,
        api_url: str,
        secret: str,
    ):
        self.organization_id = organization_id
        self.environment_name = environment_name
        self.api_url = api_url
        self.secret = secret


class DatadogApiKeyProvider(ResourceProvider):
    """Provider for managing Datadog API keys via cpgw."""

    def create(self, props):
        result = asyncio.run(
            asyncio.to_thread(
                create_datadog_api_key,
                organization_id=props["organization_id"],
                environment_name=props["environment_name"],
                api_url=props["api_url"],
                secret=props["secret"],
            )
        )

        return CreateResult(
            result.key_id,
            {**props, "api_key": result.api_key, "key_id": result.key_id},
        )

    def diff(self, id, olds, news):
        # Replace if organization_id or environment_name changes
        replaces = []
        if olds.get("organization_id") != news.get("organization_id"):
            replaces.append("organization_id")
        if olds.get("environment_name") != news.get("environment_name"):
            replaces.append("environment_name")
        return DiffResult(
            changes=len(replaces) > 0,
            replaces=replaces,
            stables=["api_key", "key_id"],
            delete_before_replace=True,
        )

    def delete(self, id, props):
        organization_id = props.get("organization_id")
        key_id = props.get("key_id")
        api_url = props.get("api_url")
        secret = props.get("secret")
        # skip delete if we don't have the required props (state corruption)
        if not all([organization_id, key_id, api_url, secret]):
            return {}
        asyncio.run(
            asyncio.to_thread(
                delete_datadog_api_key,
                organization_id=organization_id,
                key_id=key_id,
                api_url=api_url,
                secret=secret,
            )
        )
        return {}


class DatadogApiKey(Resource):
    """
    A Datadog API Key resource managed via cpgw.

    Creates a per-environment Datadog API key for sending metrics and traces.
    """

    id: Output[str]
    api_key: Output[str]
    key_id: Output[str]

    def __init__(
        self,
        name: str,
        args: DatadogApiKeyArgs,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        full_args = {
            "id": None,
            "api_key": None,
            "key_id": None,
            "organization_id": args.organization_id,
            "environment_name": args.environment_name,
            "api_url": args.api_url,
            "secret": args.secret,
        }
        super().__init__(
            DatadogApiKeyProvider(),
            name,
            full_args,
            opts,
        )
