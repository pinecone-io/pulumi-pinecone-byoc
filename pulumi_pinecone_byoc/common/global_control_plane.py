CONTROL_PLANE_DEFAULTS = {
    "api_url": "https://api.pinecone.io",
    "global_env": "prod",
    "auth0_domain": "https://login.pinecone.io",
    "gcp_project": "production-pinecone",
}


def apply_defaults(args) -> None:
    for name, default in CONTROL_PLANE_DEFAULTS.items():
        if hasattr(args, name) and getattr(args, name) is None:
            setattr(args, name, default)
