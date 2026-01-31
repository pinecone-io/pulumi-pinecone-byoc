"""
Pulumi Dynamic Providers for Pinecone resources.

These providers manage Pinecone control plane resources like environments,
service accounts, and API keys through the Pulumi resource lifecycle.
"""

import asyncio
import time
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
    PineconeApiInternalError,
    create_environment,
    delete_environment,
    create_service_account,
    delete_service_account,
    create_api_key,
    delete_api_key,
    create_cpgw_api_key,
    delete_cpgw_api_key,
    create_dns_delegation,
    delete_dns_delegation,
    create_datadog_api_key,
    delete_datadog_api_key,
    create_amp_access,
    delete_amp_access,
)


# =============================================================================
# Environment Resource
# =============================================================================


class EnvironmentArgs:
    """Arguments for creating a Pinecone Environment."""

    cloud: pulumi.Input[str]
    region: pulumi.Input[str]
    global_env: pulumi.Input[str]
    api_url: pulumi.Input[str]
    secret: pulumi.Input[str]

    def __init__(
        self,
        cloud: pulumi.Input[str],
        region: pulumi.Input[str],
        global_env: pulumi.Input[str],
        api_url: pulumi.Input[str],
        secret: pulumi.Input[str],
    ):
        self.cloud = cloud
        self.region = region
        self.global_env = global_env
        self.api_url = api_url
        self.secret = secret


class EnvironmentProvider(ResourceProvider):
    """Provider for managing Pinecone environments."""

    def create(self, props):
        cloud, region, global_env, api_url, secret = (
            props["cloud"],
            props["region"],
            props["global_env"],
            props["api_url"],
            props["secret"],
        )
        environment = asyncio.run(
            asyncio.to_thread(
                create_environment,
                cloud=cloud,
                region=region,
                global_env=global_env,
                api_url=api_url,
                secret=secret,
            )
        )

        return CreateResult(
            environment.id,
            {
                **props,
                "env_name": environment.name,
                "org_id": environment.org_id,
                "org_name": environment.org_name,
            },
        )

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
        return DiffResult(
            changes=len(replaces) > 0 or olds.get("cloud") != news.get("cloud"),
            replaces=replaces,
            stables=["env_name", "org_id", "org_name"],
            delete_before_replace=True,
        )

    def update(self, id, olds, news):
        # env_name, org_id, org_name are stable, carry them forward
        return UpdateResult(
            {
                **news,
                "env_name": olds.get("env_name"),
                "org_id": olds.get("org_id"),
                "org_name": olds.get("org_name"),
            }
        )

    def delete(self, id, props):
        api_url = props.get("api_url")
        secret = props.get("secret")
        # skip delete if we don't have the required props (state corruption)
        if not all([api_url, secret]):
            return {}
        asyncio.run(
            asyncio.to_thread(
                delete_environment,
                env_id=id,
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
    org_id: Output[str]
    org_name: Output[str]

    def __init__(
        self,
        name: str,
        args: EnvironmentArgs,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        full_args = {
            "id": None,
            "env_name": None,
            "org_id": None,  # returned from api (derived from auth context)
            "org_name": None,  # returned from api (derived from auth context)
            "cloud": args.cloud,
            "region": args.region,
            "global_env": args.global_env,
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

    name: pulumi.Input[str]
    api_url: pulumi.Input[str]
    secret: pulumi.Input[str]

    def __init__(
        self,
        name: pulumi.Input[str],
        api_url: pulumi.Input[str],
        secret: pulumi.Input[str],
    ):
        self.name = name
        self.api_url = api_url
        self.secret = secret


class ServiceAccountProvider(ResourceProvider):
    """Provider for managing Pinecone service accounts."""

    def create(self, props):
        name, api_url, secret = (
            props["name"],
            props["api_url"],
            props["secret"],
        )
        service_account_id, client_id, client_secret = asyncio.run(
            asyncio.to_thread(
                create_service_account,
                name=name,
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
        # replace if name changes
        replaces = []
        if olds.get("name") != news.get("name"):
            replaces.append("name")
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
    org_id: pulumi.Input[str]
    project_name: pulumi.Input[str]
    key_name: pulumi.Input[str]
    api_url: pulumi.Input[str]
    auth0_domain: pulumi.Input[str]
    auth0_client_id: pulumi.Input[str]
    auth0_client_secret: pulumi.Input[str]

    def __init__(
        self,
        org_id: pulumi.Input[str],
        project_name: pulumi.Input[str],
        key_name: pulumi.Input[str],
        api_url: pulumi.Input[str],
        auth0_domain: pulumi.Input[str],
        auth0_client_id: pulumi.Input[str],
        auth0_client_secret: pulumi.Input[str],
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

        # replace if project_name, key_name, or auth credentials change
        replaces = []
        if olds.get("project_name") != news.get("project_name"):
            replaces.append("project_name")
        if olds.get("key_name") != news.get("key_name"):
            replaces.append("key_name")
        if olds.get("org_id") != news.get("org_id"):
            replaces.append("org_id")
        # credentials change means service account was replaced - must recreate
        if olds.get("auth0_client_id") != news.get("auth0_client_id"):
            replaces.append("auth0_client_id")
        if olds.get("auth0_client_secret") != news.get("auth0_client_secret"):
            replaces.append("auth0_client_secret")
        return DiffResult(
            changes=len(replaces) > 0,
            replaces=replaces,
            stables=["value", "api_key_id", "project_id"],
            delete_before_replace=True,
        )

    def delete(self, id, props):
        from .api import Auth0Config

        # skip delete if missing required props (state corruption)
        auth0_domain = props.get("auth0_domain")
        auth0_client_id = props.get("auth0_client_id")
        auth0_client_secret = props.get("auth0_client_secret")
        api_url = props.get("api_url")
        api_key = props.get("value")
        if not all(
            [auth0_domain, auth0_client_id, auth0_client_secret, api_url, api_key]
        ):
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
    """Arguments for creating a DNS delegation."""

    subdomain: pulumi.Input[str]
    nameservers: pulumi.Input[list[str]]
    api_url: pulumi.Input[str]
    cpgw_api_key: pulumi.Input[str]

    def __init__(
        self,
        subdomain: pulumi.Input[str],
        nameservers: pulumi.Input[list[str]],
        api_url: pulumi.Input[str],
        cpgw_api_key: pulumi.Input[str],
    ):
        self.subdomain = subdomain
        self.nameservers = nameservers
        self.api_url = api_url
        self.cpgw_api_key = cpgw_api_key


class DnsDelegationProvider(ResourceProvider):
    def create(self, props):
        result = asyncio.run(
            asyncio.to_thread(
                create_dns_delegation,
                subdomain=props["subdomain"],
                nameservers=props["nameservers"],
                api_url=props["api_url"],
                cpgw_api_key=props["cpgw_api_key"],
            )
        )

        return CreateResult(
            result.fqdn,
            {**props, "fqdn": result.fqdn, "change_id": result.change_id},
        )

    def diff(self, id, olds, news):
        # force replace if subdomain changes
        replaces = []
        if olds.get("subdomain") != news.get("subdomain"):
            replaces.append("subdomain")
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
                subdomain=news["subdomain"],
                nameservers=news["nameservers"],
                api_url=news["api_url"],
                cpgw_api_key=news["cpgw_api_key"],
            )
        )
        return UpdateResult(
            {**news, "fqdn": result.fqdn, "change_id": result.change_id}
        )

    def delete(self, id, props):
        subdomain = props.get("subdomain")
        nameservers = props.get("nameservers")
        api_url = props.get("api_url")
        cpgw_api_key = props.get("cpgw_api_key")
        # skip delete if we don't have the required props (state corruption)
        if not all([subdomain, nameservers, api_url, cpgw_api_key]):
            return {}
        asyncio.run(
            asyncio.to_thread(
                delete_dns_delegation,
                subdomain=subdomain,
                nameservers=nameservers,
                api_url=api_url,
                cpgw_api_key=cpgw_api_key,
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
            "subdomain": args.subdomain,
            "nameservers": args.nameservers,
            "api_url": args.api_url,
            "cpgw_api_key": args.cpgw_api_key,
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

    api_url: pulumi.Input[str]
    cpgw_api_key: pulumi.Input[str]

    def __init__(
        self,
        api_url: pulumi.Input[str],
        cpgw_api_key: pulumi.Input[str],
    ):
        self.api_url = api_url
        self.cpgw_api_key = cpgw_api_key


class DatadogApiKeyProvider(ResourceProvider):
    """Provider for managing Datadog API keys via cpgw."""

    def create(self, props):
        result = asyncio.run(
            asyncio.to_thread(
                create_datadog_api_key,
                api_url=props["api_url"],
                cpgw_api_key=props["cpgw_api_key"],
            )
        )

        return CreateResult(
            result.key_id,
            {**props, "api_key": result.api_key, "key_id": result.key_id},
        )

    def diff(self, id, olds, news):
        # Datadog keys are immutable - no changes supported
        return DiffResult(
            changes=False,
            replaces=[],
            stables=["api_key", "key_id"],
            delete_before_replace=True,
        )

    def delete(self, id, props):
        key_id = props.get("key_id")
        api_url = props.get("api_url")
        cpgw_api_key = props.get("cpgw_api_key")
        # skip delete if we don't have the required props (state corruption)
        if not all([key_id, api_url, cpgw_api_key]):
            return {}
        asyncio.run(
            asyncio.to_thread(
                delete_datadog_api_key,
                key_id=key_id,
                api_url=api_url,
                cpgw_api_key=cpgw_api_key,
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
            "api_url": args.api_url,
            "cpgw_api_key": args.cpgw_api_key,
        }
        super().__init__(
            DatadogApiKeyProvider(),
            name,
            full_args,
            opts,
        )


# =============================================================================
# AmpAccess Resource
# =============================================================================


class AmpAccessArgs:
    """Arguments for creating AMP access for a BYOC environment."""

    workload_role_arn: pulumi.Input[str]
    api_url: pulumi.Input[str]
    cpgw_api_key: pulumi.Input[str]

    def __init__(
        self,
        workload_role_arn: pulumi.Input[str],
        api_url: pulumi.Input[str],
        cpgw_api_key: pulumi.Input[str],
    ):
        self.workload_role_arn = workload_role_arn
        self.api_url = api_url
        self.cpgw_api_key = cpgw_api_key


class AmpAccessProvider(ResourceProvider):
    def create(self, props):
        # retry with exponential backoff for IAM eventual consistency
        # (cross-account principal validation can fail if the role was just created)
        max_retries = 5
        last_error = None

        for attempt in range(max_retries):
            if attempt > 0:
                delay = 2**attempt  # 2s, 4s, 8s, 16s
                time.sleep(delay)

            try:
                result = asyncio.run(
                    asyncio.to_thread(
                        create_amp_access,
                        workload_role_arn=props["workload_role_arn"],
                        api_url=props["api_url"],
                        cpgw_api_key=props["cpgw_api_key"],
                    )
                )

                return CreateResult(
                    props["workload_role_arn"],
                    {
                        **props,
                        "pinecone_role_arn": result.pinecone_role_arn,
                        "amp_remote_write_endpoint": result.amp_remote_write_endpoint,
                        "amp_region": result.amp_region,
                    },
                )
            except PineconeApiInternalError as e:
                last_error = e
                continue

        raise last_error or Exception("AmpAccess creation failed after retries")

    def diff(self, id, olds, news):
        # Replace if workload_role_arn changes
        changes = olds.get("workload_role_arn") != news.get("workload_role_arn")
        return DiffResult(
            changes=changes,
            replaces=[],
            stables=["pinecone_role_arn", "amp_remote_write_endpoint", "amp_region"],
            delete_before_replace=True,
        )

    def update(self, id, olds, news):
        # retry with exponential backoff for IAM eventual consistency
        max_retries = 5
        last_error = None

        for attempt in range(max_retries):
            if attempt > 0:
                delay = 2**attempt
                time.sleep(delay)

            try:
                result = asyncio.run(
                    asyncio.to_thread(
                        create_amp_access,
                        workload_role_arn=news["workload_role_arn"],
                        api_url=news["api_url"],
                        cpgw_api_key=news["cpgw_api_key"],
                    )
                )
                return UpdateResult(
                    {
                        **news,
                        "pinecone_role_arn": result.pinecone_role_arn,
                        "amp_remote_write_endpoint": result.amp_remote_write_endpoint,
                        "amp_region": result.amp_region,
                    }
                )
            except PineconeApiInternalError as e:
                last_error = e
                continue

        raise last_error or Exception("AmpAccess update failed after retries")

    def delete(self, id, props):
        api_url = props.get("api_url")
        cpgw_api_key = props.get("cpgw_api_key")
        if not all([api_url, cpgw_api_key]):
            return {}
        asyncio.run(
            asyncio.to_thread(
                delete_amp_access,
                api_url=api_url,
                cpgw_api_key=cpgw_api_key,
            )
        )
        return {}


class AmpAccess(Resource):
    id: Output[str]
    pinecone_role_arn: Output[str]
    amp_remote_write_endpoint: Output[str]
    amp_region: Output[str]

    def __init__(
        self,
        name: str,
        args: AmpAccessArgs,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        full_args = {
            "id": None,
            "pinecone_role_arn": None,
            "amp_remote_write_endpoint": None,
            "amp_region": None,
            "workload_role_arn": args.workload_role_arn,
            "api_url": args.api_url,
            "cpgw_api_key": args.cpgw_api_key,
        }
        super().__init__(
            AmpAccessProvider(),
            name,
            full_args,
            opts,
        )


# =============================================================================
# CpgwApiKey Resource
# =============================================================================


class CpgwApiKeyArgs:
    environment: pulumi.Input[str]
    api_url: pulumi.Input[str]
    pinecone_api_key: pulumi.Input[str]

    def __init__(
        self,
        environment: pulumi.Input[str],
        api_url: pulumi.Input[str],
        pinecone_api_key: pulumi.Input[str],
    ):
        self.environment = environment
        self.api_url = api_url
        self.pinecone_api_key = pinecone_api_key


class CpgwApiKeyProvider(ResourceProvider):
    def create(self, props):
        result = asyncio.run(
            asyncio.to_thread(
                create_cpgw_api_key,
                environment=props["environment"],
                api_url=props["api_url"],
                pinecone_api_key=props["pinecone_api_key"],
            )
        )

        return CreateResult(
            result.id,
            {**props, "key_id": result.id, "key": result.key},
        )

    def diff(self, id, olds, news):
        replaces = []
        if olds.get("environment") != news.get("environment"):
            replaces.append("environment")
        return DiffResult(
            changes=len(replaces) > 0,
            replaces=replaces,
            stables=["key_id", "key"],
            delete_before_replace=True,
        )

    def delete(self, id, props):
        key_id = props.get("key_id")
        api_url = props.get("api_url")
        pinecone_api_key = props.get("pinecone_api_key")
        if not all([key_id, api_url, pinecone_api_key]):
            return {}
        asyncio.run(
            asyncio.to_thread(
                delete_cpgw_api_key,
                key_id=key_id,
                api_url=api_url,
                pinecone_api_key=pinecone_api_key,
            )
        )
        return {}


class CpgwApiKey(Resource):
    id: Output[str]
    key_id: Output[str]
    key: Output[str]

    def __init__(
        self,
        name: str,
        args: CpgwApiKeyArgs,
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        full_args = {
            "id": None,
            "key_id": None,
            "key": None,
            "environment": args.environment,
            "api_url": args.api_url,
            "pinecone_api_key": args.pinecone_api_key,
        }
        super().__init__(
            CpgwApiKeyProvider(),
            name,
            full_args,
            opts,
        )
