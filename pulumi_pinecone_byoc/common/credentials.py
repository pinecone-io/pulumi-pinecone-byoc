"""Which property names hold a credential, and what to do about it.

The dynamic providers in this package return `{**props, ...}`, so a credential
handed in as an input comes back as an output. Pulumi encrypts an output only when
it is told to, and until it is told, the value sits in plaintext in state and gets
printed by anything that shows a property diff.

The list is matched by name rather than per provider on purpose: a provider that
grows an output called `value` or `key` is covered without anyone remembering to
come back here.
"""

import pulumi

SECRET_OUTPUTS = [
    "api_key",
    "auth0_client_secret",
    "client_secret",
    "cpgw_api_key",
    "datadog_api_key",
    "key",
    "pinecone_api_key",
    "secret",
    "value",
]


def with_secret_outputs(opts: pulumi.ResourceOptions | None) -> pulumi.ResourceOptions:
    """The caller's options, plus the credential outputs Pulumi should encrypt."""
    return pulumi.ResourceOptions.merge(
        opts, pulumi.ResourceOptions(additional_secret_outputs=SECRET_OUTPUTS)
    )
