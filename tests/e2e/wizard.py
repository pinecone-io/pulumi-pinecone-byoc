def parse_wizard_env(wizard_env):
    return dict(
        line.removeprefix("export ").split("=", 1)
        for line in wizard_env.splitlines()
        if line.startswith("export ")
    )
